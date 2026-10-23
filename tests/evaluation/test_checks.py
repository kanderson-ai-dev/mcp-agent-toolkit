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


def test_citation_check_flags_fabricated_url() -> None:
    call = RecordedToolCall(
        tool="web_search",
        ok=True,
        args={},
        duration_ms=5,
        output_preview='{"status":"ok","data":[{"url":"https://a.example.com"}]}',
    )
    clean = check_run(_q("st-02"), [call], "See https://a.example.com.")
    assert clean.unsupported_citations == []
    dirty = check_run(_q("st-02"), [call], "See https://made-up.example.com.")
    assert dirty.unsupported_citations == ["https://made-up.example.com"]


def test_citation_check_skipped_when_not_expected() -> None:
    call = _call("read_file")
    checks = check_run(_q("st-04"), [call], "See https://anything.example.com.")
    assert checks.citations_expected is False
    assert checks.unsupported_citations == []


def test_prompt_leak_detected_in_answer() -> None:
    template = "A" * 45 + ". Short."
    checks = check_run(_q("ad-07"), [], f"Sure — {'A' * 45}. Done.", template)
    assert checks.prompt_leaks == ["A" * 45 + "."]


def test_prompt_leak_empty_without_template() -> None:
    checks = check_run(_q("ad-07"), [], "any answer")
    assert checks.prompt_leaks == []
