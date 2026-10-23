"""Agent-event tests — wire-format parity with the oracle's SSE dump and
pretty/json formatting."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from mcp_toolkit.client.events import (
    AgentEvent,
    AnswerDeltaEvent,
    FinalEvent,
    LlmMessageEvent,
    ToolCallEvent,
    ToolResultEvent,
    UsageSummary,
    format_event,
    parse_result_envelope,
)

ORACLE_SSE = Path(__file__).parent.parent / "fixtures" / "oracle" / "sse-events.stub.json"
_adapter = TypeAdapter(AgentEvent)


def test_every_oracle_event_validates_against_our_union() -> None:
    """The dumped TypeScript E2E events must parse into our models —
    camelCase wire parity is a contract, not a coincidence."""
    dump = json.loads(ORACLE_SSE.read_text(encoding="utf-8"))
    count = 0
    for run in dump["runs"]:
        for raw in run["events"]:
            event = _adapter.validate_python(raw)
            assert event.type == raw["type"]
            count += 1
    assert count > 40  # the oracle dump has dozens of events


def test_camel_case_wire_keys() -> None:
    event = ToolResultEvent(
        iteration=1,
        request_id="r-1",
        tool="web_search",
        ok=True,
        duration_ms=12,
        preview="p",
        result=None,
    )
    wire = json.loads(event.model_dump_json(by_alias=True))
    assert wire["requestId"] == "r-1"
    assert wire["durationMs"] == 12
    assert "request_id" not in wire


def test_final_event_wire_keys() -> None:
    event = FinalEvent(content="done", iterations=2, tool_calls=3, terminated_by="answer")
    wire = json.loads(event.model_dump_json(by_alias=True))
    assert wire["toolCalls"] == 3
    assert wire["terminatedBy"] == "answer"


def test_answer_delta_wire_shape_and_roundtrip() -> None:
    """Additive event: plain fields only, parses back through the union."""
    event = AnswerDeltaEvent(iteration=1, content="Hello ")
    wire = json.loads(event.model_dump_json(by_alias=True))
    assert wire == {"type": "answer_delta", "iteration": 1, "content": "Hello "}
    assert _adapter.validate_python(wire) == event


def test_format_event_answer_delta() -> None:
    event = AnswerDeltaEvent(iteration=1, content="chunk")
    assert format_event(event, "pretty") == "chunk"
    assert json.loads(format_event(event, "json"))["type"] == "answer_delta"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"status": "ok", "data": {"x": 1}}', {"status": "ok", "data": {"x": 1}}),
        (
            '{"status": "error", "code": "forbidden", "message": "m"}',
            {"status": "error", "code": "forbidden", "message": "m"},
        ),
        ("not json", None),
        ('{"status": "weird"}', None),
        ("plain text", None),
        ("", None),
    ],
)
def test_parse_result_envelope(text: str, expected: dict[str, Any] | None) -> None:
    assert parse_result_envelope(text) == expected


def test_format_event_json_is_single_line_camel() -> None:
    event = ToolCallEvent(iteration=2, request_id="r", tool="t", args={"a": 1})
    out = format_event(event, "json")
    assert "\n" not in out
    assert json.loads(out)["requestId"] == "r"


def test_format_event_pretty() -> None:
    assert "tool_call web_search" in format_event(
        ToolCallEvent(iteration=1, request_id="r", tool="web_search", args={}), "pretty"
    )
    assert "ERROR" in format_event(
        ToolResultEvent(
            iteration=1,
            request_id="r",
            tool="t",
            ok=False,
            duration_ms=3,
            preview="bad",
        ),
        "pretty",
    )
    assert "FINAL" in format_event(
        FinalEvent(content="c", iterations=1, tool_calls=0, terminated_by="answer"),
        "pretty",
    )
    assert "llm:" in format_event(LlmMessageEvent(iteration=0, content="hi"), "pretty")


def test_final_event_telemetry_wire_shape() -> None:
    event = FinalEvent(
        content="done",
        iterations=2,
        tool_calls=1,
        terminated_by="answer",
        duration_ms=4200,
        usage=UsageSummary(prompt_tokens=900, completion_tokens=100, total_tokens=1000),
        cost_usd=0.0007,
    )
    wire = json.loads(event.model_dump_json(by_alias=True))
    assert wire["durationMs"] == 4200
    assert wire["costUsd"] == 0.0007
    assert wire["usage"] == {
        "promptTokens": 900,
        "completionTokens": 100,
        "totalTokens": 1000,
    }
    assert "cost_usd" not in wire
    assert "prompt_tokens" not in wire["usage"]


def test_final_event_null_telemetry_serializes_as_null() -> None:
    """Unknown price → explicit null on the wire, not a fabricated 0."""
    event = FinalEvent(content="x", iterations=1, tool_calls=0, terminated_by="answer")
    wire = json.loads(event.model_dump_json(by_alias=True))
    assert wire["costUsd"] is None
    assert wire["usage"] is None
    assert wire["durationMs"] == 0


def test_final_event_accepts_budget_limit() -> None:
    event = FinalEvent(content="stopped", iterations=0, tool_calls=0, terminated_by="budget_limit")
    assert json.loads(event.model_dump_json())["terminatedBy"] == "budget_limit"
