"""Metrics + observability-contract tests (Phase 5).

Every case builds a fresh ``Metrics`` on its own registry — no global
state leaks between tests. Covers counter semantics, histogram-derived
percentiles, the per-invocation log record (fields + redaction), and
client-side agent-run metrics.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import pytest
import structlog
from prometheus_client import CollectorRegistry
from structlog.testing import capture_logs

from mcp_toolkit.client.agent_loop import AgentLoopDeps, run_agent
from mcp_toolkit.client.llm.types import LLMResponse, OpenAIToolDef, ToolCallDef
from mcp_toolkit.client.mcp_client import DiscoveredTool, ToolOutcome
from mcp_toolkit.observability.metrics import Metrics, create_metrics, expose_metrics
from mcp_toolkit.result import ToolError
from mcp_toolkit.server.guardrails.rate_limiter import RateLimiter
from mcp_toolkit.server.guardrails.sql_guard import assert_read_only_select
from mcp_toolkit.server.invocation import InvocationDeps, guarded_invoke
from mcp_toolkit.server.tools.types import ToolDef
from tests.helpers import test_prompt


def _value(metrics: Metrics, name: str, labels: dict[str, str] | None = None) -> float:
    v = metrics.registry.get_sample_value(name, labels or {})
    return float(v) if v is not None else 0.0


class TestRegistryIsolation:
    def test_fresh_registries_do_not_share_samples(self) -> None:
        a = create_metrics()
        b = create_metrics()
        a.record_invocation("web_search", ok=True, error_code=None, duration_s=0.01)
        assert _value(a, "mcp_tool_invocations_total", {"tool": "web_search", "status": "ok"}) == 1
        assert _value(b, "mcp_tool_invocations_total", {"tool": "web_search", "status": "ok"}) == 0

    def test_supplied_registry_is_used(self) -> None:
        reg = CollectorRegistry()
        m = create_metrics(reg)
        assert m.registry is reg


class TestCounters:
    def test_ok_invocation_increments_ok_and_observes(self) -> None:
        m = create_metrics()
        m.record_invocation("db_query", ok=True, error_code=None, duration_s=0.5)
        assert _value(m, "mcp_tool_invocations_total", {"tool": "db_query", "status": "ok"}) == 1
        assert _value(m, "mcp_tool_invocations_total", {"tool": "db_query", "status": "error"}) == 0
        assert _value(m, "mcp_tool_errors_total", {"tool": "db_query", "code": "forbidden"}) == 0
        assert _value(m, "mcp_tool_duration_seconds_count", {"tool": "db_query"}) == 1
        assert _value(m, "mcp_tool_duration_seconds_sum", {"tool": "db_query"}) == pytest.approx(
            0.5
        )

    def test_error_invocation_increments_error_and_code(self) -> None:
        m = create_metrics()
        m.record_invocation("db_query", ok=False, error_code="forbidden", duration_s=0.1)
        assert _value(m, "mcp_tool_invocations_total", {"tool": "db_query", "status": "error"}) == 1
        assert _value(m, "mcp_tool_errors_total", {"tool": "db_query", "code": "forbidden"}) == 1

    def test_guardrail_block_labeled_by_guardrail(self) -> None:
        m = create_metrics()
        m.record_guardrail_block("sql_guard")
        m.record_guardrail_block("path_sandbox")
        m.record_guardrail_block("sql_guard")
        assert _value(m, "mcp_guardrail_blocks_total", {"guardrail": "sql_guard"}) == 2
        assert _value(m, "mcp_guardrail_blocks_total", {"guardrail": "path_sandbox"}) == 1
        assert _value(m, "mcp_guardrail_blocks_total", {"guardrail": "rate_limiter"}) == 0


class TestHistogram:
    def test_p95_is_derivable_from_buckets(self) -> None:
        m = create_metrics()
        for duration in [0.01] * 18 + [0.2, 6.0]:
            m.record_invocation("read_file", ok=True, error_code=None, duration_s=duration)

        buckets = {
            le: _value(m, "mcp_tool_duration_seconds_bucket", {"tool": "read_file", "le": le})
            for le in ("0.025", "0.1", "0.5", "1.0", "5.0", "15.0", "30.0", "+Inf")
        }
        # Monotonically nondecreasing cumulative counts, +Inf == count.
        counts = list(buckets.values())
        assert counts == sorted(counts)
        assert buckets["+Inf"] == 20

        # p95 = smallest bucket whose cumulative count covers 95%.
        target = 0.95 * 20
        p95 = next(
            float(le) for le, c in ((float(k), v) for k, v in buckets.items()) if c >= target
        )
        # 18 samples ≤0.025, then 0.2 and 6.0 → the 19th ordered sample
        # (0.95×20) lands in the le=0.5 bucket.
        assert p95 == 0.5

    def test_iteration_histogram_buckets(self) -> None:
        m = create_metrics()
        m.record_agent_run("answer", 2)
        m.record_agent_run("iteration_limit", 8)
        assert (
            _value(m, "agent_runs_total", {"terminated_by": "answer", "prompt_version": "none"})
            == 1
        )
        assert (
            _value(
                m,
                "agent_runs_total",
                {"terminated_by": "iteration_limit", "prompt_version": "none"},
            )
            == 1
        )
        assert _value(m, "agent_iterations_count") == 2
        assert _value(m, "agent_iterations_sum") == pytest.approx(10)
        assert _value(m, "agent_iterations_bucket", {"le": "5.0"}) == 1
        assert _value(m, "agent_iterations_bucket", {"le": "13.0"}) == 2


class TestExposition:
    def test_metrics_and_healthz_endpoints(self) -> None:
        m = create_metrics()
        m.record_invocation("web_search", ok=True, error_code=None, duration_s=0.01)
        server = expose_metrics(m.registry, 0, structlog.get_logger("test"))
        try:
            port = server.server_address[1]
            body = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5).read()
            assert b"mcp_tool_invocations_total" in body
            health = urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5).read()
            assert health == b"ok"
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=5)
            assert exc.value.code == 404
        finally:
            server.shutdown()


def _deps(metrics: Metrics | None = None, **overrides: Any) -> InvocationDeps:
    return InvocationDeps(
        limiter=overrides.get("limiter", RateLimiter(60)),
        session_id="sess-test",
        max_output_chars=overrides.get("max_output_chars", 12_000),
        logger=structlog.get_logger("test"),
        metrics=metrics,
    )


def _tool(handler: Any, name: str = "t") -> ToolDef:
    return ToolDef(name=name, description="d", handler=handler)


class TestInvocationInstrumentation:
    async def test_ok_invocation_records_metrics(self) -> None:
        m = create_metrics()

        async def handler(**_kw: Any) -> dict[str, Any]:
            return {"v": 1}

        res = await guarded_invoke(_tool(handler, "db_query"), {}, None, _deps(m))
        assert res.is_error is False
        assert _value(m, "mcp_tool_invocations_total", {"tool": "db_query", "status": "ok"}) == 1
        assert _value(m, "mcp_tool_duration_seconds_count", {"tool": "db_query"}) == 1

    async def test_guardrail_error_increments_block_counter(self) -> None:
        m = create_metrics()

        async def handler(query: str, **_kw: Any) -> dict[str, Any]:
            assert_read_only_select(query)
            return {}

        res = await guarded_invoke(
            _tool(handler, "db_query"), {"query": "DROP TABLE x"}, None, _deps(m)
        )
        assert res.is_error is True
        assert _value(m, "mcp_tool_errors_total", {"tool": "db_query", "code": "forbidden"}) == 1
        assert _value(m, "mcp_guardrail_blocks_total", {"guardrail": "sql_guard"}) == 1

    async def test_rate_limit_block_attributed(self) -> None:
        m = create_metrics()
        limiter = RateLimiter(1)
        limiter.check("t")
        limiter.record("t")

        async def handler(**_kw: Any) -> dict[str, Any]:
            return {}

        res = await guarded_invoke(_tool(handler), {}, None, _deps(m, limiter=limiter))
        assert res.is_error is True
        assert _value(m, "mcp_guardrail_blocks_total", {"guardrail": "rate_limiter"}) == 1
        assert _value(m, "mcp_tool_errors_total", {"tool": "t", "code": "rate_limited"}) == 1

    async def test_unexpected_error_is_internal_not_guardrail(self) -> None:
        m = create_metrics()

        async def handler(**_kw: Any) -> dict[str, Any]:
            raise RuntimeError("boom")

        await guarded_invoke(_tool(handler), {}, None, _deps(m))
        assert _value(m, "mcp_tool_errors_total", {"tool": "t", "code": "internal"}) == 1
        assert _value(m, "mcp_guardrail_blocks_total") == 0

    async def test_no_metrics_is_a_noop(self) -> None:
        async def handler(**_kw: Any) -> dict[str, Any]:
            return {}

        res = await guarded_invoke(_tool(handler), {}, None, _deps(None))
        assert res.is_error is False


class TestInvocationLogRecord:
    async def test_one_record_with_required_fields(self) -> None:
        async def handler(**_kw: Any) -> dict[str, Any]:
            return {"v": 1}

        with capture_logs() as logs:
            await guarded_invoke(_tool(handler, "db_query"), {"query": "SELECT 1"}, None, _deps())
        invocations = [e for e in logs if e.get("event") == "tool_invocation"]
        assert len(invocations) == 1
        rec = invocations[0]
        assert rec["tool"] == "db_query"
        assert rec["request_id"]
        assert rec["ok"] is True
        assert rec["error_code"] is None
        assert isinstance(rec["duration_ms"], int)
        assert rec["args"] == {"query": "str:8"}

    async def test_error_record_carries_code(self) -> None:
        async def handler(**_kw: Any) -> dict[str, Any]:
            raise ToolError("not_found", "missing")

        with capture_logs() as logs:
            await guarded_invoke(_tool(handler, "read_file"), {"path": "x"}, None, _deps())
        rec = next(e for e in logs if e.get("event") == "tool_invocation")
        assert rec["ok"] is False
        assert rec["error_code"] == "not_found"

    async def test_write_file_content_never_reaches_logs(self) -> None:
        """A secret pasted into write_file content must not appear in any
        log field — only its length survives via the arg shape."""
        secret = "sk-proj-9f8e7d6c5b4a-topsecret"  # noqa: S105 — fake key for redaction test

        async def handler(**_kw: Any) -> dict[str, Any]:
            return {"bytes": 1}

        with capture_logs() as logs:
            await guarded_invoke(
                _tool(handler, "write_file"),
                {"path": "a.md", "content": secret},
                None,
                _deps(),
            )
        rendered = json.dumps(logs)
        assert secret not in rendered
        rec = next(e for e in logs if e.get("event") == "tool_invocation")
        assert rec["args"] == {"path": "str:4", "content": f"str:{len(secret)}"}
        assert rec["blocked_by"] is None


class _StaticLLM:
    name = "static"
    model = "static-model"

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)

    async def complete(self, _m: Any, _t: list[OpenAIToolDef]) -> LLMResponse:
        return self.responses.pop(0)


class _FakeInvoker:
    def list_tools(self) -> list[DiscoveredTool]:
        return []

    async def call(self, _name: str, _args: dict[str, Any], _rid: str) -> ToolOutcome:
        return ToolOutcome(ok=True, text='{"status":"ok","data":{}}', duration_ms=1)


class TestAgentMetrics:
    async def test_agent_run_metrics_recorded(self) -> None:
        m = create_metrics()
        llm = _StaticLLM(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[ToolCallDef(id="1", name="web_search", arguments="{}")],
                ),
                LLMResponse(content="done"),
            ]
        )
        deps = AgentLoopDeps(
            llm=llm,  # type: ignore[arg-type]
            invoker=_FakeInvoker(),
            tools=[],
            max_iterations=5,
            emit=lambda _e: None,
            prompt=test_prompt(),
            metrics=m,
        )
        result = await run_agent("q", deps)
        assert result.terminated_by == "answer"
        assert (
            _value(
                m,
                "agent_runs_total",
                {"terminated_by": "answer", "prompt_version": "agent_system@v1"},
            )
            == 1
        )
        assert _value(m, "agent_iterations_count") == 1
        assert _value(m, "agent_iterations_sum") == pytest.approx(2)

    async def test_iteration_limit_run_labeled(self) -> None:
        m = create_metrics()
        llm = _StaticLLM(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[ToolCallDef(id="1", name="web_search", arguments="{}")],
                )
            ]
            * 2
        )
        deps = AgentLoopDeps(
            llm=llm,  # type: ignore[arg-type]
            invoker=_FakeInvoker(),
            tools=[],
            max_iterations=2,
            emit=lambda _e: None,
            prompt=test_prompt(),
            metrics=m,
        )
        await run_agent("q", deps)
        assert (
            _value(
                m,
                "agent_runs_total",
                {"terminated_by": "iteration_limit", "prompt_version": "agent_system@v1"},
            )
            == 1
        )
