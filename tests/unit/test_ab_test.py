"""A/B gate unit tests — gate math, tolerance, mode honesty and the
judge_rubric same-transcript rule."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from evaluation.dataset import EvalQuestion, load_dataset
from evaluation.judge import JudgeScores
from evaluation.run_eval import EvalDeps, QuestionOutput
from evaluation.scorecard import Aggregate

from mcp_toolkit.client.llm.stub_llm import StubLLM
from mcp_toolkit.client.mcp_client import DiscoveredTool, ToolOutcome
from mcp_toolkit.prompts.ab_test import (
    SideResult,
    compute_gate,
    run_ab,
)
from mcp_toolkit.prompts.loader import LoadedPrompt
from tests.helpers import test_prompt


class FakeInvoker:
    async def call(self, name: str, args: dict[str, Any], request_id: str) -> ToolOutcome:
        return ToolOutcome(ok=True, text='{"status": "ok", "data": {}}', duration_ms=1)

    def list_tools(self) -> list[DiscoveredTool]:
        return []


def _tools() -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": n, "description": "", "parameters": {}}}
        for n in ("web_search", "db_query", "read_file", "write_file")
    ]


def _deps(prompt: LoadedPrompt) -> EvalDeps:
    return EvalDeps(
        llm=StubLLM(),
        invoker=FakeInvoker(),
        tools=_tools(),
        max_iterations=8,
        agent_prompt=prompt,
    )


def _loaded(name: str, version: int, template: str | None = None) -> LoadedPrompt:
    t = template or f"{name} v{version} template"
    return LoadedPrompt(
        name=name,
        version=version,
        sha256=hashlib.sha256(t.encode()).hexdigest(),
        template=t,
    )


def _questions(n: int = 3) -> list[EvalQuestion]:
    return load_dataset().questions[:n]


def _aggregate(
    *,
    task: float | None = 5.0,
    ground: float | None = 5.0,
    tools: float | None = 5.0,
    tool_pct: float = 1.0,
    resisted: str = "1/1",
) -> Aggregate:
    return Aggregate(
        task_success_avg=task,
        grounding_avg=ground,
        tool_usage_avg=tools,
        tool_usage_appropriate_pct=tool_pct,
        adversarial_resisted=resisted,
        passed=True,
        failures=[],
    )


def _side(**kw: Any) -> SideResult:
    return SideResult(aggregate=_aggregate(**kw), entries=[])


# ── compute_gate ──────────────────────────────────────────────────────


def test_gate_passes_within_tolerance() -> None:
    deltas, regressions, passed = compute_gate(_side(task=5.0), _side(task=4.92), judged=True)
    assert passed is True
    assert regressions == []
    assert deltas["taskSuccess"] == pytest.approx(-0.08)


def test_gate_fails_on_task_success_regression() -> None:
    deltas, regressions, passed = compute_gate(_side(task=5.0), _side(task=4.7), judged=True)
    assert passed is False
    assert deltas["taskSuccess"] == pytest.approx(-0.3)
    assert any(r.startswith("taskSuccess") for r in regressions)


def test_gate_fails_on_grounding_regression() -> None:
    _d, regressions, passed = compute_gate(_side(ground=4.5), _side(ground=4.3), judged=True)
    assert passed is False
    assert any(r.startswith("grounding") for r in regressions)


def test_gate_fails_on_tool_usage_regression() -> None:
    _d, regressions, passed = compute_gate(_side(tools=5.0), _side(tools=4.8), judged=True)
    assert passed is False
    assert any(r.startswith("toolUsage") for r in regressions)


def test_gate_fails_on_tool_pct_regression() -> None:
    _d, regressions, passed = compute_gate(_side(tool_pct=1.0), _side(tool_pct=0.85), judged=True)
    assert passed is False
    assert any(r.startswith("toolUsageAppropriatePct") for r in regressions)


def test_gate_adversarial_zero_tolerance() -> None:
    _d, regressions, passed = compute_gate(
        _side(resisted="3/3"), _side(resisted="2/3"), judged=True
    )
    assert passed is False
    assert any(r.startswith("adversarialResisted") for r in regressions)


def test_gate_adversarial_improvement_allowed() -> None:
    _d, _r, passed = compute_gate(_side(resisted="2/3"), _side(resisted="3/3"), judged=True)
    assert passed is True


def test_gate_unjudged_still_checks_deterministic_metrics() -> None:
    _d, regressions, passed = compute_gate(
        _side(task=None, ground=None, tools=None, tool_pct=1.0),
        _side(task=None, ground=None, tools=None, tool_pct=0.5),
        judged=False,
    )
    assert passed is False
    assert any(r.startswith("toolUsageAppropriatePct") for r in regressions)


# ── run_ab — agent_system ─────────────────────────────────────────────


async def test_run_ab_offline_smoke_never_promotable() -> None:
    baseline, candidate = _loaded("agent_system", 1), _loaded("agent_system", 2)
    result = await run_ab(
        "agent_system",
        baseline,
        candidate,
        questions=_questions(2),
        deps=_deps(test_prompt()),
        judge=None,
        mode="offline-smoke",
    )
    assert result.judged is False
    assert result.gate_passed is True  # no regressions — mechanics work
    assert result.promotion_allowed is False  # but it's smoke, never evidence
    assert result.baseline.aggregate.task_success_avg is None


async def test_run_ab_live_gate_pass_and_same_rubric() -> None:
    """Both sides run the same dataset under one judge rubric."""
    rubrics_seen: list[str] = []

    async def judge(q: EvalQuestion, o: QuestionOutput, rubric: str) -> JudgeScores:
        rubrics_seen.append(rubric)
        return JudgeScores(
            task_success=5,
            grounding=5,
            tool_usage=5,
            adversarial_resistance=True,
            rationale="ok",
        )

    baseline, candidate = _loaded("agent_system", 1), _loaded("agent_system", 2)
    questions = _questions(3)
    result = await run_ab(
        "agent_system",
        baseline,
        candidate,
        questions=questions,
        deps=_deps(baseline),
        judge=judge,
        mode="live",
        judge_template="THE RUBRIC v1",
    )
    assert result.judged and result.gate_passed and result.promotion_allowed
    assert len(rubrics_seen) == 2 * len(questions)
    assert set(rubrics_seen) == {"THE RUBRIC v1"}
    assert result.baseline.aggregate.task_success_avg == 5
    assert result.candidate.aggregate.task_success_avg == 5


async def test_run_ab_detects_candidate_regression() -> None:
    """Judge scores the first half (baseline) 5, second half (candidate) 4."""
    calls = 0

    async def judge(q: EvalQuestion, o: QuestionOutput, rubric: str) -> JudgeScores:
        nonlocal calls
        calls += 1
        score = 5.0 if calls <= 3 else 4.0
        return JudgeScores(
            task_success=score,
            grounding=score,
            tool_usage=score,
            adversarial_resistance=True,
            rationale="ok",
        )

    questions = _questions(3)
    result = await run_ab(
        "agent_system",
        _loaded("agent_system", 1),
        _loaded("agent_system", 2),
        questions=questions,
        deps=_deps(test_prompt()),
        judge=judge,
        mode="live",
        judge_template="rubric",
    )
    assert result.gate_passed is False
    assert result.promotion_allowed is False
    assert result.deltas["taskSuccess"] == pytest.approx(-1.0)
    assert any(r.startswith("taskSuccess") for r in result.regressions)


async def test_run_ab_agent_requires_rubric_when_judged() -> None:
    with pytest.raises(ValueError, match="judge rubric"):
        await run_ab(
            "agent_system",
            _loaded("agent_system", 1),
            _loaded("agent_system", 2),
            questions=_questions(1),
            deps=_deps(test_prompt()),
            judge=lambda q, o, r: None,  # type: ignore[arg-type,return-value]
            mode="live",
        )


# ── run_ab — judge_rubric ─────────────────────────────────────────────


async def test_judge_rubric_ab_scores_same_transcripts_under_both() -> None:
    """One agent run; identical transcripts scored under each rubric —
    the candidate rubric only varies the judge's prompt."""
    rubrics_seen: list[str] = []

    async def judge(q: EvalQuestion, o: QuestionOutput, rubric: str) -> JudgeScores:
        rubrics_seen.append(rubric)
        # Rubric v2 is stricter → honest regression measured on identical data.
        score = 5.0 if "STRICT" not in rubric else 4.0
        return JudgeScores(
            task_success=score,
            grounding=score,
            tool_usage=score,
            adversarial_resistance=True,
            rationale="ok",
        )

    questions = _questions(3)
    baseline = _loaded("judge_rubric", 1, "RUBRIC v1")
    candidate = _loaded("judge_rubric", 2, "RUBRIC v2 STRICT")
    agent = _loaded("agent_system", 1)

    result = await run_ab(
        "judge_rubric",
        baseline,
        candidate,
        questions=questions,
        deps=_deps(agent),
        judge=judge,
        mode="live",
        agent_prompt=agent,
    )
    # Each entry judged twice (once per rubric), dataset ran once.
    assert len(rubrics_seen) == 2 * len(questions)
    assert rubrics_seen.count("RUBRIC v1") == len(questions)
    assert rubrics_seen.count("RUBRIC v2 STRICT") == len(questions)
    assert result.gate_passed is False
    assert result.deltas["taskSuccess"] == pytest.approx(-1.0)


async def test_judge_rubric_ab_requires_agent_prompt() -> None:
    with pytest.raises(ValueError, match="agent prompt"):
        await run_ab(
            "judge_rubric",
            _loaded("judge_rubric", 1),
            _loaded("judge_rubric", 2),
            questions=_questions(1),
            deps=_deps(test_prompt()),
            judge=None,
            mode="offline-smoke",
        )


# ── persistence helpers (web/ab_runs.py) ──────────────────────────────


def test_save_and_latest_ab_result_round_trip(tmp_path: Any) -> None:
    from mcp_toolkit.web.ab_runs import latest_ab_result, save_ab_result

    async def _make() -> Any:
        return await run_ab(
            "agent_system",
            _loaded("agent_system", 1),
            _loaded("agent_system", 2),
            questions=_questions(1),
            deps=_deps(test_prompt()),
            judge=None,
            mode="offline-smoke",
        )

    import asyncio

    result = asyncio.run(_make())
    path = save_ab_result(result, tmp_path)
    assert path.is_file()
    wire = latest_ab_result("agent_system", 2, tmp_path)
    assert wire is not None
    assert wire["candidateVersion"] == 2
    assert wire["mode"] == "offline-smoke"
    # Wrong name/version miss the file.
    assert latest_ab_result("judge_rubric", 2, tmp_path) is None
    assert latest_ab_result("agent_system", 9, tmp_path) is None


def test_latest_ab_result_skips_corrupt_files(tmp_path: Any) -> None:
    from mcp_toolkit.web.ab_runs import latest_ab_result

    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "agent_system-b1-c2-bad-20260101.json").write_text("{not json")
    (tmp_path / "agent_system-b1-c99-good-20260102.json").write_text(
        '{"candidateVersion": 99, "mode": "live"}'
    )
    wire = latest_ab_result("agent_system", 2, tmp_path)
    assert wire is None  # corrupt + wrong-version both skipped
    wire99 = latest_ab_result("agent_system", 99, tmp_path)
    assert wire99 is not None and wire99["candidateVersion"] == 99
    assert latest_ab_result("agent_system", 1, tmp_path / "missing") is None


async def test_to_wire_uses_camel_keys() -> None:
    result = await run_ab(
        "agent_system",
        _loaded("agent_system", 1),
        _loaded("agent_system", 2),
        questions=_questions(1),
        deps=_deps(test_prompt()),
        judge=None,
        mode="offline-smoke",
    )
    wire = result.to_wire()
    for key in (
        "baselineVersion",
        "candidateSha256",
        "gatePassed",
        "promotionAllowed",
        "mode",
        "deltas",
        "regressions",
    ):
        assert key in wire
    assert wire["mode"] == "offline-smoke"
    assert wire["promotionAllowed"] is False
