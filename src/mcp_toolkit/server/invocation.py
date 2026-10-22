"""Guarded tool invocation — the per-call pipeline every tool goes
through, in deliberate order:

1. request-ID extraction (``params._meta.requestId`` → JSON-RPC id),
2. rate-limit check + record (cheap reject first; a rejected attempt
   never consumes budget),
3. tool handler (args already schema-validated by the SDK),
4. error → typed envelope (never a thrown protocol error),
5. output sanitization + cap (tool output is untrusted data),
6. structured log of the invocation.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from mcp.server.mcpserver.context import Context
from mcp.types import CallToolResult, TextContent

from mcp_toolkit.observability.request_context import (
    bind_request_id,
    unbind_request_id,
)
from mcp_toolkit.result import (
    ErrEnvelope,
    ToolError,
    envelope_json,
    error_result,
    ok,
)
from mcp_toolkit.server.guardrails.rate_limiter import RateLimiter
from mcp_toolkit.server.guardrails.sanitize import sanitize_tool_output
from mcp_toolkit.server.tools.types import ToolContext, ToolDef


@dataclass(frozen=True)
class InvocationDeps:
    limiter: RateLimiter
    session_id: str
    max_output_chars: int
    logger: structlog.stdlib.BoundLogger | structlog.typing.FilteringBoundLogger


def extract_request_id(ctx: Context[Any, Any] | None) -> str:
    """Client-supplied ``params._meta.requestId`` when present, else the
    JSON-RPC request ID assigned by the SDK, else a fresh UUID."""
    if ctx is not None:
        try:
            request_ctx = ctx.request_context
        except ValueError:
            request_ctx = None
        if request_ctx is not None:
            meta = request_ctx.meta
            rid = (
                meta.get("requestId")
                if isinstance(meta, dict)
                else getattr(meta, "requestId", None)
            )
            if isinstance(rid, str) and rid:
                return rid
            if request_ctx.request_id is not None:
                return str(request_ctx.request_id)
    return str(uuid.uuid4())


def _preview(value: Any, max_chars: int = 500) -> str:
    """Bounded single-line preview of tool args/output for the log line."""
    try:
        text = json.dumps(value, default=str)
        return text if len(text) <= max_chars else f"{text[:max_chars]}…"
    except (TypeError, ValueError):
        return "[unserializable]"


async def guarded_invoke(
    tool: ToolDef,
    args: dict[str, Any],
    ctx: Context[Any, Any] | None,
    deps: InvocationDeps,
) -> CallToolResult:
    """Run ``tool.handler`` inside the guardrail chain → CallToolResult."""
    started = time.perf_counter()
    request_id = extract_request_id(ctx)
    token = bind_request_id(request_id)
    log = deps.logger
    fields: dict[str, Any] = {
        "tool": tool.name,
        "session_id": deps.session_id,
    }
    try:
        try:
            deps.limiter.check(tool.name)
            deps.limiter.record(tool.name)
        except ToolError as exc:
            log.warning(
                "tool_invocation_rate_limited",
                **fields,
                ok=False,
                error=str(exc),
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
            return _result(error_result(exc), deps)

        try:
            data = await tool.handler(
                **args, ctx=ToolContext(request_id=request_id, session_id=deps.session_id)
            )
            result = _result(ok(data), deps)
            log.info(
                "tool_invocation",
                **fields,
                ok=not result.is_error,
                input=_preview(args),
                output=_preview([c.model_dump() for c in result.content]),
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
            return result
        except Exception as exc:
            # ToolError keeps its code; anything else is an internal failure.
            result = _result(error_result(exc), deps)
            log.warning(
                "tool_invocation_failed",
                **fields,
                ok=False,
                error=str(exc),
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
            return result
    finally:
        unbind_request_id(token)


def _result(envelope: Any, deps: InvocationDeps) -> CallToolResult:
    """Serialize the envelope, sanitize the text (untrusted data), and
    flag errors via ``is_error`` so the model can read and react."""
    is_error = isinstance(envelope, ErrEnvelope)
    text = sanitize_tool_output(envelope_json(envelope), deps.max_output_chars).text
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=is_error)
