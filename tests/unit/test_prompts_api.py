"""Prompt-registry API tests — status matrix, admin gate, A/B flow,
gated promotion with override, rollback and hot-swap."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from evaluation.scorecard import Aggregate

from mcp_toolkit.client.llm.types import LLMResponse
from mcp_toolkit.client.mcp_client import DiscoveredTool, ToolOutcome
from mcp_toolkit.prompts.ab_test import ABResult, SideResult
from mcp_toolkit.prompts.loader import LoadedPrompt
from mcp_toolkit.prompts.registry import PromptRegistry
from mcp_toolkit.web.ab_runs import AbRunManager
from mcp_toolkit.web.app import WebDeps, create_app
from mcp_toolkit.web.prompts_routes import PromptAPIDeps
from tests.helpers import copy_promoted_baseline, test_prompt


def _registry(tmp_path: Path) -> PromptRegistry:
    """A copy of the real registry pinned to the promoted v1 baseline."""
    root = copy_promoted_baseline(Path("prompts"), tmp_path / "prompts")
    return PromptRegistry(root, audit_path=tmp_path / "audit.jsonl")


class FakeInvoker:
    async def call(self, name: str, args: dict[str, Any], request_id: str) -> ToolOutcome:
        return ToolOutcome(ok=True, text='{"status":"ok","data":{}}', duration_ms=1)

    def list_tools(self) -> list[DiscoveredTool]:
        return []


class ScriptedLLM:
    name = "scripted"
    model = "scripted-model"

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)

    async def complete(
        self, _m: Any, _t: Any, on_delta: Callable[[str], None] | None = None
    ) -> LLMResponse:
        return self._responses.pop(0)


def _api_deps(
    registry: PromptRegistry,
    *,
    admin: bool = True,
    runner: Any = None,
    manager: AbRunManager | None = None,
    ab_results_dir: Path | None = None,
) -> PromptAPIDeps:
    return PromptAPIDeps(
        registry=registry,
        admin_enabled=admin,
        ab_manager=manager or AbRunManager(),
        make_ab_runner=runner,
        ab_results_dir=ab_results_dir,
    )


def _web_deps(prompt_api: PromptAPIDeps, **overrides: Any) -> WebDeps:
    return WebDeps(
        invoker=FakeInvoker(),
        tools=[],
        discovered_tools=[],
        llm=overrides.pop("llm", ScriptedLLM([])),
        max_iterations=4,
        agent_prompt=test_prompt(),
        prompt_api=prompt_api,
        **overrides,
    )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _aggregate(**kw: Any) -> Aggregate:
    defaults: dict[str, Any] = {
        "task_success_avg": 5.0,
        "grounding_avg": 5.0,
        "tool_usage_avg": 5.0,
        "tool_usage_appropriate_pct": 1.0,
        "adversarial_resisted": "1/1",
        "passed": True,
        "failures": [],
    }
    defaults.update(kw)
    return Aggregate(**defaults)


def _ab_result(
    name: str = "agent_system",
    candidate_version: int = 2,
    *,
    mode: str = "live",
    gate: bool = True,
    regressions: list[str] | None = None,
) -> ABResult:
    regs = regressions if regressions is not None else ([] if gate else ["taskSuccess: 5 → 4"])
    return ABResult(
        name=name,
        baseline_version=1,
        candidate_version=candidate_version,
        baseline_sha256="b" * 64,
        candidate_sha256="c" * 64,
        mode=mode,  # type: ignore[arg-type]
        judged=True,
        baseline=SideResult(_aggregate(), []),
        candidate=SideResult(_aggregate(), []),
        deltas={},
        regressions=regs,
        gate_passed=gate,
    )


def _wire_evidence(
    name: str = "agent_system",
    candidate_version: int = 2,
    *,
    promotion_allowed: bool = True,
    regressions: list[str] | None = None,
    mode: str = "live",
) -> dict[str, Any]:
    return {
        "name": name,
        "baselineVersion": 1,
        "candidateVersion": candidate_version,
        "baselineSha256": "b" * 64,
        "candidateSha256": "c" * 64,
        "mode": mode,
        "judged": True,
        "baseline": {},
        "candidate": {},
        "deltas": {},
        "regressions": regressions or [],
        "gatePassed": promotion_allowed,
        "promotionAllowed": promotion_allowed,
    }


# ── reads ─────────────────────────────────────────────────────────────


class TestReads:
    async def test_list_prompts(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        app = create_app(_web_deps(_api_deps(reg)))
        async with _client(app) as c:
            res = await c.get("/api/prompts")
        assert res.status_code == 200
        body = res.json()["prompts"]
        assert set(body) == {"agent_system", "judge_rubric"}
        assert body["agent_system"][0]["status"] == "promoted"
        # The console's read-only switch rides on this flag.
        assert res.json()["adminEnabled"] is True

    async def test_list_prompts_reports_admin_enabled(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        app = create_app(_web_deps(_api_deps(reg, admin=True)))
        async with _client(app) as c:
            res = await c.get("/api/prompts")
        assert res.json()["adminEnabled"] is True

    async def test_ab_result_endpoint(self, tmp_path: Path) -> None:
        """GET /{name}/ab-result?candidate=v — 404 without evidence, the
        persisted wire result when a completed run saved it, 400 without the
        candidate param."""
        reg = _registry(tmp_path)
        out_dir = tmp_path / "ab"
        app = create_app(_web_deps(_api_deps(reg, ab_results_dir=out_dir)))
        async with _client(app) as c:
            missing = await c.get("/api/prompts/agent_system/ab-result?candidate=1")
            no_param = await c.get("/api/prompts/agent_system/ab-result")
            wire = {
                "name": "agent_system",
                "baselineVersion": 1,
                "candidateVersion": 1,
                "baselineSha256": "a" * 64,
                "candidateSha256": "b" * 64,
                "mode": "live",
                "judged": True,
                "baseline": {},
                "candidate": {},
                "deltas": {},
                "regressions": [],
                "gatePassed": True,
                "promotionAllowed": True,
            }
            out_dir.mkdir(parents=True, exist_ok=True)
            fname = f"agent_system-b1-c1-{'b' * 8}-20261023-000000.json"
            (out_dir / fname).write_text(json.dumps(wire), encoding="utf-8")
            found = await c.get("/api/prompts/agent_system/ab-result?candidate=1")
        assert missing.status_code == 404
        assert no_param.status_code == 400
        assert found.status_code == 200
        assert found.json()["candidateVersion"] == 1

    async def test_show_name_and_version(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        app = create_app(_web_deps(_api_deps(reg)))
        async with _client(app) as c:
            name = await c.get("/api/prompts/agent_system")
            one = await c.get("/api/prompts/agent_system/1")
        assert name.status_code == 200
        assert name.json()["promotedVersion"] == 1
        assert one.status_code == 200
        assert one.json()["version"] == 1
        assert "template" in one.json()

    async def test_unknown_name_and_version_404(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        app = create_app(_web_deps(_api_deps(reg)))
        async with _client(app) as c:
            missing_name = await c.get("/api/prompts/nope")
            missing_version = await c.get("/api/prompts/agent_system/99")
        assert missing_name.status_code == 404
        assert missing_version.status_code == 404

    async def test_diff_requires_params_and_versions(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        reg.create_candidate(
            "agent_system", template="v2 template text", description="v2", author="t"
        )
        app = create_app(_web_deps(_api_deps(reg)))
        async with _client(app) as c:
            ok = await c.get("/api/prompts/agent_system/diff", params={"from": 1, "to": 2})
            missing = await c.get("/api/prompts/agent_system/diff")
            bad = await c.get("/api/prompts/agent_system/diff", params={"from": 1, "to": 99})
        assert ok.status_code == 200
        assert "-You are" in ok.json()["diff"] or "v2 template text" in ok.json()["diff"]
        assert missing.status_code == 400
        assert bad.status_code == 404

    async def test_audit_endpoint_reports_chain(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        reg.create_candidate("agent_system", template="t", description="d", author="t")
        app = create_app(_web_deps(_api_deps(reg)))
        async with _client(app) as c:
            res = await c.get("/api/prompts/audit")
        assert res.status_code == 200
        body = res.json()
        assert body["verified"] is True
        assert body["events"][0]["type"] == "candidate_created"


# ── admin gate ────────────────────────────────────────────────────────


class TestAdminGate:
    @pytest.mark.parametrize(
        ("method", "path", "payload"),
        [
            ("post", "/api/prompts/agent_system/candidates", {"template": "x", "description": "d"}),
            ("post", "/api/prompts/agent_system/ab-test", {"candidate_version": 2}),
            ("post", "/api/prompts/agent_system/promote", {"version": 2}),
            ("post", "/api/prompts/agent_system/rollback", {"to_version": 1, "reason": "r"}),
        ],
    )
    async def test_mutations_403_when_admin_disabled(
        self, tmp_path: Path, method: str, path: str, payload: dict[str, Any]
    ) -> None:
        reg = _registry(tmp_path)
        app = create_app(_web_deps(_api_deps(reg, admin=False)))
        async with _client(app) as c:
            res = await c.request(method, path, json=payload)
        assert res.status_code == 403
        assert res.json()["code"] == "admin_disabled"

    async def test_reads_open_when_admin_disabled(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        app = create_app(_web_deps(_api_deps(reg, admin=False)))
        async with _client(app) as c:
            assert (await c.get("/api/prompts")).status_code == 200
            assert (await c.get("/api/prompts/agent_system")).status_code == 200


# ── candidates ────────────────────────────────────────────────────────


class TestCandidates:
    async def test_create_candidate_201(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        app = create_app(_web_deps(_api_deps(reg)))
        async with _client(app) as c:
            res = await c.post(
                "/api/prompts/agent_system/candidates",
                json={
                    "template": "You are a better agent.",
                    "description": "v2",
                    "author": "tester",
                },
            )
        assert res.status_code == 201
        assert res.json()["version"] == 2
        assert res.json()["status"] == "candidate"

    async def test_template_with_key_like_string_rejected(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        app = create_app(_web_deps(_api_deps(reg)))
        async with _client(app) as c:
            res = await c.post(
                "/api/prompts/agent_system/candidates",
                json={"template": "key is sk-abcdefghijklmnop1234", "description": "d"},
            )
        assert res.status_code == 400
        assert res.json()["code"] == "invalid_input"

    async def test_template_over_limit_rejected(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        app = create_app(_web_deps(_api_deps(reg)))
        async with _client(app) as c:
            res = await c.post(
                "/api/prompts/agent_system/candidates",
                json={"template": "x" * 8001, "description": "d"},
            )
        assert res.status_code == 400

    async def test_body_over_32kb_rejected(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        app = create_app(_web_deps(_api_deps(reg)))
        async with _client(app) as c:
            res = await c.post(
                "/api/prompts/agent_system/candidates",
                json={"template": "x" * 7000, "description": "d" * 30_000},
            )
        assert res.status_code == 400

    async def test_candidate_for_unknown_name_404(self, tmp_path: Path) -> None:
        """A brand-new prompt name is refused — a candidate-only lineage
        would break the one-promoted invariant on reload."""
        reg = _registry(tmp_path)
        app = create_app(_web_deps(_api_deps(reg)))
        async with _client(app) as c:
            res = await c.post(
                "/api/prompts/agent_system_v2/candidates",
                json={"template": "x", "description": "d"},
            )
        assert res.status_code == 404


# ── A/B test endpoint ─────────────────────────────────────────────────


class TestAbEndpoint:
    async def test_start_ab_returns_run_id_and_completes(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        reg.create_candidate("agent_system", template="v2", description="v2", author="t")
        result = _ab_result(candidate_version=2)

        async def runner(*_a: Any, progress: Any = None, **_k: Any) -> ABResult:
            return result

        app = create_app(_web_deps(_api_deps(reg, runner=runner, ab_results_dir=tmp_path / "ab")))
        async with _client(app) as c:
            res = await c.post("/api/prompts/agent_system/ab-test", json={"candidate_version": 2})
            assert res.status_code == 202
            run_id = res.json()["runId"]
            for _ in range(50):
                status = (await c.get(f"/api/prompts/ab/{run_id}")).json()
                if status["status"] != "running":
                    break
                await asyncio.sleep(0.01)
            final = await c.get(f"/api/prompts/ab/{run_id}")
        assert final.json()["status"] == "completed"
        assert final.json()["result"]["gatePassed"] is True
        # The ab_test event was audited.
        types = [e["type"] for e in reg.audit_log.read()]
        assert "ab_test" in types

    async def test_ab_status_404_for_unknown_run(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        app = create_app(_web_deps(_api_deps(reg, runner=lambda *a: None)))
        async with _client(app) as c:
            res = await c.get("/api/prompts/ab/nonexistent")
        assert res.status_code == 404

    async def test_ab_unknown_candidate_404(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        app = create_app(_web_deps(_api_deps(reg, runner=lambda *a: None)))
        async with _client(app) as c:
            res = await c.post("/api/prompts/agent_system/ab-test", json={"candidate_version": 42})
        assert res.status_code == 404

    async def test_concurrent_ab_runs_409(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        reg.create_candidate("agent_system", template="v2", description="v2", author="t")
        gate = asyncio.Event()

        async def runner(*_a: Any, **_k: Any) -> ABResult:
            await gate.wait()
            return _ab_result()

        app = create_app(_web_deps(_api_deps(reg, runner=runner)))
        async with _client(app) as c:
            first = await c.post("/api/prompts/agent_system/ab-test", json={"candidate_version": 2})
            second = await c.post(
                "/api/prompts/agent_system/ab-test", json={"candidate_version": 2}
            )
        gate.set()
        assert first.status_code == 202
        assert second.status_code == 409

    async def test_ab_stream_delivers_progress_and_result(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        reg.create_candidate("agent_system", template="v2", description="v2", author="t")
        result = _ab_result(candidate_version=2)

        async def runner(_n: Any, _b: Any, _c: Any, _o: Any, progress: Any, **_k: Any) -> ABResult:
            progress("starting baseline")
            return result

        app = create_app(_web_deps(_api_deps(reg, runner=runner, ab_results_dir=tmp_path / "ab")))
        async with _client(app) as c:
            res = await c.post("/api/prompts/agent_system/ab-test", json={"candidate_version": 2})
            run_id = res.json()["runId"]
            for _ in range(50):
                if (await c.get(f"/api/prompts/ab/{run_id}")).json()["status"] != "running":
                    break
                await asyncio.sleep(0.01)
            stream = await c.get(f"/api/prompts/ab/{run_id}/stream")
        assert stream.status_code == 200
        assert "event: progress" in stream.text
        assert "event: result" in stream.text
        assert "gatePassed" in stream.text

    async def test_ab_stream_unknown_run_reports_error(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        app = create_app(_web_deps(_api_deps(reg, runner=lambda *a: None)))
        async with _client(app) as c:
            res = await c.get("/api/prompts/ab/missing/stream")
        assert res.status_code == 200
        assert "event: error" in res.text

    async def test_ab_runner_failure_marks_run_error(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        reg.create_candidate("agent_system", template="v2", description="v2", author="t")

        async def runner(*_a: Any, **_k: Any) -> ABResult:
            raise RuntimeError("eval stack exploded")

        app = create_app(_web_deps(_api_deps(reg, runner=runner, ab_results_dir=tmp_path / "ab")))
        async with _client(app) as c:
            res = await c.post("/api/prompts/agent_system/ab-test", json={"candidate_version": 2})
            run_id = res.json()["runId"]
            for _ in range(50):
                status = (await c.get(f"/api/prompts/ab/{run_id}")).json()
                if status["status"] != "running":
                    break
                await asyncio.sleep(0.01)
        assert status["status"] == "error"
        assert "eval stack exploded" in status["error"]

    async def test_ab_without_runner_configured_503(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        reg.create_candidate("agent_system", template="v2", description="v2", author="t")
        app = create_app(_web_deps(_api_deps(reg, runner=None)))
        async with _client(app) as c:
            res = await c.post("/api/prompts/agent_system/ab-test", json={"candidate_version": 2})
        assert res.status_code == 503
        assert res.json()["code"] == "live_unavailable"


# ── promote ───────────────────────────────────────────────────────────


class TestPromote:
    def _candidate(self, reg: PromptRegistry) -> None:
        reg.create_candidate("agent_system", template="v2 text", description="v2", author="t")

    def _plant_evidence(self, tmp_path: Path, **kw: Any) -> Path:
        """Drop a persisted A/B result file into the tmp evidence dir —
        the same artifact a real `ab-test` run would leave."""
        out_dir = tmp_path / "ab"
        out_dir.mkdir(exist_ok=True)
        wire = _wire_evidence(**kw)
        (out_dir / "agent_system-b1-c2-deadbeef-20260101.json").write_text(
            json.dumps(wire), encoding="utf-8"
        )
        return out_dir

    async def test_promote_without_evidence_409(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        self._candidate(reg)
        app = create_app(_web_deps(_api_deps(reg, ab_results_dir=tmp_path / "ab")))
        async with _client(app) as c:
            res = await c.post("/api/prompts/agent_system/promote", json={"version": 2})
        assert res.status_code == 409
        assert res.json()["code"] == "no_ab_evidence"
        assert reg.get_promoted("agent_system").version == 1

    async def test_promote_with_passing_evidence_200(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        self._candidate(reg)
        out_dir = self._plant_evidence(tmp_path, promotion_allowed=True)
        app = create_app(_web_deps(_api_deps(reg, ab_results_dir=out_dir)))
        async with _client(app) as c:
            res = await c.post("/api/prompts/agent_system/promote", json={"version": 2})
        assert res.status_code == 200
        assert reg.get_promoted("agent_system").version == 2
        assert reg.get("agent_system", 1).status == "rolled_back"

    async def test_promote_blocked_on_failed_gate_with_regressions(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        self._candidate(reg)
        out_dir = self._plant_evidence(
            tmp_path,
            promotion_allowed=False,
            regressions=["taskSuccess: 5 → 4.5"],
            mode="live",
        )
        app = create_app(_web_deps(_api_deps(reg, ab_results_dir=out_dir)))
        async with _client(app) as c:
            res = await c.post("/api/prompts/agent_system/promote", json={"version": 2})
        assert res.status_code == 409
        assert res.json()["code"] == "gate_failed"
        assert res.json()["regressions"] == ["taskSuccess: 5 → 4.5"]
        assert reg.get_promoted("agent_system").version == 1

    async def test_promote_offline_smoke_evidence_blocked(self, tmp_path: Path) -> None:
        """`promotionAllowed` is only ever true for live runs — an
        offline smoke file can never promote."""
        reg = _registry(tmp_path)
        self._candidate(reg)
        out_dir = self._plant_evidence(
            tmp_path,
            promotion_allowed=False,
            regressions=[],
            mode="offline-smoke",
        )
        app = create_app(_web_deps(_api_deps(reg, ab_results_dir=out_dir)))
        async with _client(app) as c:
            res = await c.post("/api/prompts/agent_system/promote", json={"version": 2})
        assert res.status_code == 409
        assert res.json()["mode"] == "offline-smoke"

    async def test_promote_override_succeeds_and_audits_reason(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        self._candidate(reg)
        out_dir = self._plant_evidence(
            tmp_path,
            promotion_allowed=False,
            regressions=["taskSuccess: 5 → 4.5"],
        )
        app = create_app(_web_deps(_api_deps(reg, ab_results_dir=out_dir)))
        async with _client(app) as c:
            res = await c.post(
                "/api/prompts/agent_system/promote",
                json={"version": 2, "override_reason": "human reviewed; judge noise"},
            )
        assert res.status_code == 200
        assert reg.get_promoted("agent_system").version == 2
        types = [e["type"] for e in reg.audit_log.read()]
        assert "promoted_override" in types
        override = next(e for e in reg.audit_log.read() if e["type"] == "promoted_override")
        assert override["override_reason"] == "human reviewed; judge noise"

    async def test_promote_with_inmemory_ab_evidence(self, tmp_path: Path) -> None:
        """``ab_id`` pins evidence to a specific completed run — no disk
        file needed."""
        reg = _registry(tmp_path)
        self._candidate(reg)
        manager = AbRunManager()

        async def runner(*_a: Any, **_k: Any) -> ABResult:
            return _ab_result(candidate_version=2)

        app = create_app(
            _web_deps(
                _api_deps(reg, runner=runner, manager=manager, ab_results_dir=tmp_path / "ab")
            )
        )
        async with _client(app) as c:
            start = await c.post("/api/prompts/agent_system/ab-test", json={"candidate_version": 2})
            run_id = start.json()["runId"]
            for _ in range(50):
                if manager.get(run_id).status != "running":  # type: ignore[union-attr]
                    break
                await asyncio.sleep(0.01)
            res = await c.post(
                "/api/prompts/agent_system/promote",
                json={"version": 2, "ab_id": run_id},
            )
        assert res.status_code == 200
        assert reg.get_promoted("agent_system").version == 2

    async def test_promote_ab_id_for_different_version_409(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        self._candidate(reg)
        manager = AbRunManager()

        async def runner(*_a: Any, **_k: Any) -> ABResult:
            return _ab_result(candidate_version=2)

        app = create_app(
            _web_deps(
                _api_deps(reg, runner=runner, manager=manager, ab_results_dir=tmp_path / "ab")
            )
        )
        async with _client(app) as c:
            start = await c.post("/api/prompts/agent_system/ab-test", json={"candidate_version": 2})
            run_id = start.json()["runId"]
            for _ in range(50):
                if manager.get(run_id).status != "running":  # type: ignore[union-attr]
                    break
                await asyncio.sleep(0.01)
            res = await c.post(
                "/api/prompts/agent_system/promote",
                json={"version": 2, "ab_id": "nonexistent"},
            )
        assert res.status_code == 409

    async def test_promote_non_candidate_400(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        app = create_app(_web_deps(_api_deps(reg)))
        async with _client(app) as c:
            res = await c.post("/api/prompts/agent_system/promote", json={"version": 1})
        assert res.status_code == 400

    async def test_promote_unknown_version_404(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        app = create_app(_web_deps(_api_deps(reg)))
        async with _client(app) as c:
            res = await c.post("/api/prompts/agent_system/promote", json={"version": 99})
        assert res.status_code == 404


# ── rollback ──────────────────────────────────────────────────────────


class TestRollback:
    async def test_rollback_restores_exactly_one_promoted(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        reg.create_candidate("agent_system", template="v2", description="v2", author="t")
        reg.promote("agent_system", 2, reason="gate")
        assert reg.get_promoted("agent_system").version == 2
        app = create_app(_web_deps(_api_deps(reg)))
        async with _client(app) as c:
            res = await c.post(
                "/api/prompts/agent_system/rollback",
                json={"to_version": 1, "reason": "v2 regressed"},
            )
        assert res.status_code == 200
        promoted = [a for a in reg.list("agent_system") if a.status == "promoted"]
        assert len(promoted) == 1 and promoted[0].version == 1
        assert reg.get("agent_system", 2).status == "rolled_back"

    async def test_rollback_unknown_version_404(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path)
        app = create_app(_web_deps(_api_deps(reg)))
        async with _client(app) as c:
            res = await c.post(
                "/api/prompts/agent_system/rollback",
                json={"to_version": 77, "reason": "x"},
            )
        assert res.status_code == 404


# ── hot-swap ──────────────────────────────────────────────────────────


class TestHotSwap:
    async def test_next_run_uses_new_version_inflight_keeps_old(self, tmp_path: Path) -> None:
        """prompt_provider resolves per run: run started before promotion
        finishes with v1; the next run sees v2."""
        reg = _registry(tmp_path)
        reg.create_candidate("agent_system", template="v2 prompt", description="v2", author="t")

        versions_seen: list[int] = []

        def provider() -> LoadedPrompt:
            a = reg.get_promoted("agent_system")
            versions_seen.append(a.version)
            return LoadedPrompt(
                name=a.name, version=a.version, sha256=a.sha256, template=a.template
            )

        gate = asyncio.Event()

        class GatedLLM:
            name = "gated"
            model = "gated-model"

            async def complete(
                self, messages: Any, _t: Any, on_delta: Callable[[str], None] | None = None
            ) -> LLMResponse:
                await gate.wait()
                return LLMResponse(content="done")

        deps = _web_deps(
            _api_deps(reg),
            llm=GatedLLM(),
            prompt_provider=provider,
        )
        app = create_app(deps)
        async with _client(app) as c:
            run1 = await c.post("/api/chat", json={"question": "question one"})
            assert run1.status_code == 201

            async def stream(run_id: str) -> str:
                res = await c.get("/api/chat/stream", params={"runId": run_id})
                return res.text

            # Start run1 — provider resolves v1, LLM blocks on the gate.
            task1 = asyncio.create_task(stream(run1.json()["runId"]))
            for _ in range(100):
                if versions_seen:
                    break
                await asyncio.sleep(0.01)
            assert versions_seen == [1]

            # Promote v2 while run1 is in flight; run2 must resolve v2.
            reg.promote("agent_system", 2, reason="gate")
            run2 = await c.post("/api/chat", json={"question": "question two"})
            task2 = asyncio.create_task(stream(run2.json()["runId"]))
            for _ in range(100):
                if len(versions_seen) == 2:
                    break
                await asyncio.sleep(0.01)
            assert versions_seen == [1, 2]

            gate.set()
            body1, body2 = await task1, await task2

        # run1's final carries v1, run2's carries v2 — in-flight kept its prompt.
        assert '"version": 1' in body1 or '"version":1' in body1
        assert '"version": 2' in body2 or '"version":2' in body2
