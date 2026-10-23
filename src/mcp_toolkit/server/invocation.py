"""Guarded tool invocation — the per-call pipeline every tool goes
through, in deliberate order:

1. request-ID extraction (``params._meta.requestId`` → JSON-RPC id),
2. rate-limit check + record (cheap reject first; a rejected attempt
   never consumes budget),
3. tool handler (args already schema-validated by the SDK),
4. error → typed envelope (never a thrown protocol error),
5. output sanitization + cap (tool output is untrusted data),
6. one structured log record + metric sample per invocation.

The log record carries argument *shape* (type and size per field) — never
raw values, so a ``write_file`` payload containing a pasted secret cannot
leak through the logs.
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

from mcp_toolkit.observability.metrics import Metrics
from mcp_toolkit.observability.request_context import (
    bind_request_id,
    unbind_request_id,
)
from mcp_toolkit.result import (
    ErrEnvelope,
    ToolError,
    envelope_json,
    err,
    error_result,
    ok,
)
from mcp_toolkit.server.guardrails.rate_limiter import ToolLimiter
from mcp_toolkit.server.guardrails.sanitize import sanitize_tool_output
from mcp_toolkit.server.tools.types import ToolContext, ToolDef


@dataclass(frozen=True)
class InvocationDeps:
    limiter: ToolLimiter
    session_id: str
    max_output_chars: int
    logger: structlog.stdlib.BoundLogger | structlog.typing.FilteringBoundLogger
    metrics: Metrics | None = None


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


def _arg_shape(args: dict[str, Any]) -> dict[str, str]:
    """Type+size per argument (``"str:42"``, ``"int"``, ``"list:3"``) —
    never the values themselves. String lengths reveal nothing an
    attacker could not already infer from the result."""
    shape: dict[str, str] = {}
    for key, value in args.items():
        if isinstance(value, str):
            shape[key] = f"str:{len(value)}"
        elif isinstance(value, bool):
            shape[key] = "bool"
        elif isinstance(value, int | float):
            shape[key] = type(value).__name__
        elif isinstance(value, list | tuple | dict | set):
            shape[key] = f"{type(value).__name__}:{len(value)}"
        elif value is None:
            shape[key] = "null"
        else:
            shape[key] = type(value).__name__
    return shape


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
    error_code: str | None = None
    blocked_by: str | None = None
    try:
        try:
            deps.limiter.check(tool.name)
            deps.limiter.record(tool.name)
        except ToolError as exc:
            error_code = exc.code
            blocked_by = exc.guardrail or "rate_limiter"
            result = _result(error_result(exc), deps)
        else:
            try:
                data = await tool.handler(
                    **args,
                    ctx=ToolContext(request_id=request_id, session_id=deps.session_id),
                )
                result = _result(ok(data), deps)
            except Exception as exc:
                if isinstance(exc, ToolError):
                    error_code = exc.code
                    blocked_by = exc.guardrail
                else:
                    error_code = "internal"
                result = _result(error_result(exc), deps)

        duration_s = time.perf_counter() - started
        succeeded = not result.is_error
        if deps.metrics is not None:
            deps.metrics.record_invocation(tool.name, succeeded, error_code, duration_s)
            if blocked_by is not None:
                deps.metrics.record_guardrail_block(blocked_by)
        deps.logger.info(
            "tool_invocation",
            tool=tool.name,
            session_id=deps.session_id,
            request_id=request_id,
            ok=succeeded,
            error_code=error_code,
            blocked_by=blocked_by,
            args=_arg_shape(args),
            output_chars=(
                len(result.content[0].text)
                if result.content and isinstance(result.content[0], TextContent)
                else 0
            ),
            duration_ms=round(duration_s * 1000),
        )
        return result
    finally:
        unbind_request_id(token)


def _result(envelope: Any, deps: InvocationDeps) -> CallToolResult:
    """Serialize the envelope, sanitize the text (untrusted data), and
    flag errors via ``is_error`` so the model can read and react.

    ``structuredContent`` is the *same* sanitized envelope parsed back —
    the text and structured views can never disagree. One exception:
    when the character cap truncates the text mid-JSON there is no
    faithful structure to emit, so the result degrades to a typed
    ``too_large`` error arm (and ``is_error``) — a loud failure beats a
    corrupt structure, and ``is_error`` also exempts the SDK's
    output-schema validation, which would reject the error arm."""
    text = sanitize_tool_output(envelope_json(envelope), deps.max_output_chars).text
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, dict):
        truncated = err(
            "too_large",
            f"Tool output exceeded the {deps.max_output_chars}-character cap "
            "and was truncated mid-envelope; retry with a smaller read or query",
        )
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            is_error=True,
            structured_content=truncated.model_dump(mode="json"),
        )
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        is_error=isinstance(envelope, ErrEnvelope),
        structured_content=parsed,
    )
