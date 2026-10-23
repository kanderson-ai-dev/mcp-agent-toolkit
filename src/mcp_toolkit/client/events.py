"""Structured agent event stream. Emitted on stdout so the transcript is
machine-readable (``OUTPUT_FORMAT=json`` → one JSON object per line) or
human-readable (``pretty``). The wire format is camelCase — the same
shapes the TypeScript oracle emits, so SSE consumers can sit on top of
this union unchanged.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class _EventModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, serialize_by_alias=True
    )


class ToolCallEvent(_EventModel):
    type: Literal["tool_call"] = "tool_call"
    iteration: int
    request_id: str
    tool: str
    args: dict[str, Any]


class ToolResultEvent(_EventModel):
    type: Literal["tool_result"] = "tool_result"
    iteration: int
    request_id: str
    tool: str
    ok: bool
    duration_ms: int
    preview: str
    result: dict[str, Any] | None = None
    """Parsed tool envelope when the output was our JSON contract."""


class LlmMessageEvent(_EventModel):
    type: Literal["llm_message"] = "llm_message"
    iteration: int
    content: str


class PromptVersionRef(_EventModel):
    """Provenance of the prompt that produced a run — name, registry
    version and template fingerprint."""

    name: str
    version: int
    sha256: str


class UsageSummary(_EventModel):
    """Accumulated token accounting for the run — provider-reported."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class FinalEvent(_EventModel):
    type: Literal["final"] = "final"
    content: str
    iterations: int
    tool_calls: int
    terminated_by: Literal["answer", "iteration_limit", "budget_limit"]
    duration_ms: int = 0
    """Wall-clock run time."""
    usage: UsageSummary | None = None
    """Additive/optional: accumulated prompt/completion tokens."""
    cost_usd: float | None = None
    """Additive/optional: USD cost — ``null`` when the model's price is
    unknown (never estimated)."""
    prompt_version: PromptVersionRef | None = None
    """Additive/optional: which registered prompt produced this run."""


class ErrorEvent(_EventModel):
    type: Literal["error"] = "error"
    message: str


AgentEvent = Annotated[
    ToolCallEvent | ToolResultEvent | LlmMessageEvent | FinalEvent | ErrorEvent,
    Field(discriminator="type"),
]


def parse_result_envelope(text: str) -> dict[str, Any] | None:
    """Best-effort parse of the tool's ``{status, …}`` JSON envelope.
    Non-contract output (timeouts, transport errors) yields ``None`` —
    the event still carries ``preview`` as a fallback."""
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(parsed, dict) and parsed.get("status") in ("ok", "error"):
        return parsed
    return None


def _truncate(text: str, n: int) -> str:
    return text if len(text) <= n else f"{text[: n - 1]}…"


def format_event(event: AgentEvent, fmt: Literal["pretty", "json"]) -> str:
    if fmt == "json":
        return event.model_dump_json(by_alias=True)
    match event:
        case ToolCallEvent():
            return (
                f"[iter {event.iteration}] → tool_call {event.tool} "
                f"{_truncate(json.dumps(event.args), 160)}"
            )
        case ToolResultEvent():
            status = "ok" if event.ok else "ERROR"
            return (
                f"[iter {event.iteration}] ← {status} {event.tool} "
                f"({event.duration_ms}ms) {_truncate(event.preview, 140)}"
            )
        case LlmMessageEvent():
            return f"[iter {event.iteration}] llm: {_truncate(event.content, 140)}"
        case FinalEvent():
            telemetry = ""
            if event.usage is not None:
                telemetry = f", ~{event.usage.total_tokens} tokens"
            if event.cost_usd is not None:
                telemetry += f", ~${event.cost_usd:.4f}"
            return (
                f"\n=== FINAL ({event.iterations} iterations, {event.tool_calls} "
                f"tool calls, {event.terminated_by}, "
                f"{event.duration_ms / 1000:.1f}s{telemetry}) ===\n{event.content}"
            )
        case ErrorEvent():
            return f"error: {event.message}"
