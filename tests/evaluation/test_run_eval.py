"""run_eval tests — offline harness end to end, gate logic, schema."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from evaluation.dataset import load_dataset
from evaluation.judge import JudgeScores
from evaluation.run_eval import (
    EvalDeps,
    QuestionOutput,
    build_entry,
    run_evaluation,
    run_question,
    summarize,
)
from evaluation.scorecard import CheckResult, Scorecard, ScorecardEntry

from mcp_toolkit.client.llm.stub_llm import StubLLM
from mcp_toolkit.client.mcp_client import DiscoveredTool, ToolOutcome
from tests.helpers import test_prompt


class FakeInvoker:
    """Answers every tool call with a canned ok envelope (no transport)."""

    async def call(self, name: str, args: dict[str, Any], request_id: str) -> ToolOutcome:
        return ToolOutcome(ok=True, text='{"status": "ok", "data": {}}', duration_ms=1)

    def list_tools(self) -> list[DiscoveredTool]:
        return []


def _tools(*names: str) -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": n, "description": "", "parameters": {}}}
        for n in names
    ]


def _deps() -> EvalDeps:
    return EvalDeps(
        llm=StubLLM(),
        invoker=FakeInvoker(),
        tools=_tools("web_search", "db_query", "read_file", "write_file"),
        max_iterations=8,
        agent_prompt=test_prompt(),
    )


async def test_run_question_records_tool_calls_from_events() -> None:
    out = await run_question(_deps(), "research MCP and save a report")
    assert out.terminated_by == "answer"
    assert [c.tool for c in out.tool_calls] == ["db_query", "web_search", "write_file"]
    assert all(c.ok for c in out.tool_calls)


async def test_offline_run_builds_entries_with_checks() -> None:
    questions = [q for q in load_dataset().questions if q.id in ("mt-01", "am-01")]
    entries = await run_evaluation(questions, _deps())
    assert len(entries) == 2
    mt = entries[0]
    assert mt.checks.expected_tools_called is True
    assert mt.checks.tool_call_count == 3
    am = entries[1]
    # StubLLM still uses tools on a normal question — the deterministic
    # check only requires that expected tools were called (none here).
    assert am.checks.expected_tools_called is True


def _entry(
    qid: str,
    category: str = "single_tool",
    *,
    expected: bool = True,
    forbidden: list[str] | None = None,
    citations_expected: bool = False,
    unsupported: list[str] | None = None,
    leaks: list[str] | None = None,
    judge: JudgeScores | None = None,
) -> ScorecardEntry:
    return ScorecardEntry(
        id=qid,
        category=category,
        prompt="p",
        tool_calls=[],
        iterations=1,
        terminated_by="answer",
        duration_ms=1,
        checks=CheckResult(
            expected_tools_called=expected,
            missing_tools=[] if expected else ["db_query"],
            forbidden_successes=forbidden or [],
            tool_call_count=1,
            citations_expected=citations_expected,
            unsupported_citations=unsupported or [],
            prompt_leaks=leaks or [],
        ),
        judge=judge,
    )


def _scores(
    task: float = 5, ground: float = 5, tools: float = 5, adv: bool | None = None
) -> JudgeScores:
    return JudgeScores(
        task_success=task,
        grounding=ground,
        tool_usage=tools,
        adversarial_resistance=adv,
        rationale="ok",
    )


def test_summarize_passes_when_all_thresholds_met() -> None:
    entries = [
        _entry("a", judge=_scores()),
        _entry("ad-01", category="adversarial", judge=_scores(adv=True)),
    ]
    summary = summarize(entries, judged=True)
    assert summary.passed is True
    assert summary.failures == []
    assert summary.task_success_avg == 5
    assert summary.adversarial_resisted == "1/1"


def test_gate_fails_on_low_task_success() -> None:
    entries = [_entry("a", judge=_scores(task=2)), _entry("b", judge=_scores(task=2))]
    summary = summarize(entries, judged=True)
    assert summary.passed is False
    assert any(f.startswith("task_success") for f in summary.failures)


def test_gate_fails_on_forbidden_success_even_with_good_judge() -> None:
    entries = [
        _entry(
            "ad-01",
            category="adversarial",
            forbidden=["db_query"],
            judge=_scores(adv=True),
        )
    ]
    summary = summarize(entries, judged=True)
    assert summary.passed is False
    assert any("adversarial_resistance" in f for f in summary.failures)
    assert summary.tool_usage_appropriate_pct == 0


def test_gate_fails_when_tool_usage_inappropriate() -> None:
    entries = [_entry("a", expected=False, judge=_scores()) for _ in range(3)]
    entries.append(_entry("b", judge=_scores()))
    summary = summarize(entries, judged=True)
    assert summary.tool_usage_appropriate_pct == 0.25
    assert summary.passed is False


def test_unjudged_summary_reports_nulls_and_never_fails() -> None:
    summary = summarize([_entry("a")], judged=False)
    assert summary.task_success_avg is None
    assert summary.passed is True


def test_citation_gate_fails_on_unsupported_citation() -> None:
    entries = [
        _entry("st-02", citations_expected=True, unsupported=["https://fake.example.com"]),
        _entry("mt-01", citations_expected=True),
    ]
    summary = summarize(entries, judged=True)
    assert summary.citation_verified_pct == 0.5
    assert summary.passed is False
    assert any(f.startswith("citation_verified") for f in summary.failures)


def test_citation_gate_passes_when_all_verify() -> None:
    entries = [
        _entry("st-02", citations_expected=True),
        _entry("mt-01", citations_expected=True),
        _entry("am-01"),  # not citation-expecting — excluded from the metric
    ]
    summary = summarize(entries, judged=True)
    assert summary.citation_verified_pct == 1.0
    assert summary.passed is True


def test_citation_pct_none_without_citation_rows() -> None:
    summary = summarize([_entry("am-01")], judged=True)
    assert summary.citation_verified_pct is None


def test_prompt_leak_counts_as_adversarial_failure() -> None:
    entries = [
        _entry(
            "ad-07",
            category="adversarial",
            leaks=["Evidence rules: call web_search and db_query …"],
            judge=_scores(adv=True),
        )
    ]
    summary = summarize(entries, judged=True)
    assert summary.adversarial_resisted == "0/1"
    assert summary.passed is False


def test_build_entry_truncates_answer_and_records_error() -> None:
    q = load_dataset().questions[0]
    out = QuestionOutput(
        final_content="x" * 2000,
        tool_calls=[],
        iterations=1,
        terminated_by="answer",
        error="boom",
    )
    entry = build_entry(q, out, 42)
    assert len(entry.answer_preview) == 800
    assert entry.error == "boom"
    assert entry.duration_ms == 42


def test_scorecard_accepts_oracle_wire_shape() -> None:
    """The committed TypeScript oracle scorecard validates against this schema."""
    oracle = json.loads(Path("tests/fixtures/oracle/scorecard.json").read_text(encoding="utf-8"))
    card = Scorecard.model_validate(oracle)
    assert card.dataset_version == "1.1.0"
    assert card.mode == "live"
    assert len(card.results) == 14
    assert card.aggregate.passed is True


def test_committed_schema_matches_model() -> None:
    """evaluation/scorecard.schema.json must be regenerated on model change."""
    committed = json.loads(Path("evaluation/scorecard.schema.json").read_text(encoding="utf-8"))
    committed.pop("$schema", None)
    assert committed == Scorecard.model_json_schema()


def test_scorecard_round_trips_camel_case() -> None:
    card = Scorecard(
        dataset_version="1.1.0",
        ran_at="2026-01-01T00:00:00Z",
        mode="live",
        model="m",
        judge_model="m",
        aggregate=summarize([_entry("a", judge=_scores())], judged=True),
        results=[_entry("a", judge=_scores())],
    )
    wire = json.loads(card.model_dump_json(by_alias=True))
    assert wire["datasetVersion"] == "1.1.0"
    assert wire["results"][0]["durationMs"] == 1
    assert wire["results"][0]["checks"]["expectedToolsCalled"] is True
    assert wire["thresholds"]["taskSuccess"] == 4.0


def test_summarize_aggregates_telemetry() -> None:
    """Mean tokens/cost per question + the per-prompt rollup."""
    e1 = _entry("a", judge=_scores())
    e1.usage = {"promptTokens": 800, "completionTokens": 200, "totalTokens": 1000}
    e1.cost_usd = 0.001
    e1.prompt_version = {"name": "agent_system", "version": 3, "sha256": "x"}
    e2 = _entry("b", judge=_scores())
    e2.usage = {"promptTokens": 1800, "completionTokens": 200, "totalTokens": 2000}
    e2.cost_usd = 0.003
    e2.prompt_version = {"name": "agent_system", "version": 3, "sha256": "x"}
    e3 = _entry("c", judge=_scores())  # no usage/cost — uninstrumented row

    summary = summarize([e1, e2, e3], judged=True)
    assert summary.avg_total_tokens == pytest.approx(1500.0)
    assert summary.avg_cost_usd == pytest.approx(0.002)
    bucket = summary.by_prompt["agent_system@v3"]
    assert bucket.runs == 2
    assert bucket.avg_total_tokens == pytest.approx(1500.0)
    assert bucket.avg_cost_usd == pytest.approx(0.002)
    assert summary.by_prompt["?@v?"].runs == 1


def test_summarize_telemetry_none_without_usage() -> None:
    summary = summarize([_entry("a", judge=_scores())], judged=True)
    assert summary.avg_total_tokens is None
    assert summary.avg_cost_usd is None
    # Every run still lands in the rollup, keyed by its prompt version.
    assert summary.by_prompt["?@v?"].runs == 1
    assert summary.by_prompt["?@v?"].avg_total_tokens is None
