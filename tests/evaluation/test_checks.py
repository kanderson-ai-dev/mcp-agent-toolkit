"""Deterministic-check tests — the floor under the LLM-as-judge."""

from __future__ import annotations

from evaluation.checks import RecordedToolCall, check_run
from evaluation.dataset import load_dataset


def _q(qid: str):
    return next(q for q in load_dataset().questions if q.id == qid)


def _call(tool: str, ok: bool = True) -> RecordedToolCall:
    return RecordedToolCall(tool=tool, ok=ok, args={}, duration_ms=5)


def test_expected_tools_all_called() -> None:
    checks = check_run(_q("mt-01"), [_call("db_query"), _call("web_search"), _call("write_file")])
    assert checks.expected_tools_called is True
    assert checks.missing_tools == []
    assert checks.tool_call_count == 3


def test_missing_tool_reported() -> None:
    checks = check_run(_q("mt-01"), [_call("db_query")])
    assert checks.expected_tools_called is False
    assert set(checks.missing_tools) == {"web_search", "write_file"}


def test_forbidden_success_is_a_failure() -> None:
    # ad-01 forbids a SUCCESSFUL db_query/write_file — a blocked attempt
    # is fine, a success means the guardrail failed.
    blocked = check_run(_q("ad-01"), [_call("db_query", ok=False)])
    assert blocked.forbidden_successes == []

    succeeded = check_run(_q("ad-01"), [_call("db_query", ok=True)])
    assert succeeded.forbidden_successes == ["db_query"]


def test_no_expected_tools_any_calls_count() -> None:
    checks = check_run(_q("am-01"), [])
    assert checks.expected_tools_called is True
    assert checks.tool_call_count == 0
