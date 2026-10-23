"""Thin wrapper around the official MCP client.

Canonical usage spawns the server over stdio; the client discovers
tools at runtime and invokes them with a normalized outcome — timeouts,
unknown tools and malformed payloads become typed results instead of
thrown exceptions.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.client.stdio import get_default_environment
from mcp.shared.exceptions import MCPError
from mcp.types import RequestParamsMeta

from mcp_toolkit.client.llm.types import OpenAIToolDef

CALL_TIMEOUT_S = 30.0

# Env vars the spawned server is allowed to see. Everything else —
# notably the client's OPENAI_API_KEY — is never forwarded.
SERVER_ENV_KEYS = (
    "SEARCH_PROVIDER",
    "SEARCH_API_KEY",
    "SANDBOX_ROOT",
    "DB_PATH",
    "RATE_LIMIT_PER_MINUTE",
    "RATE_LIMIT_BACKEND",
    "RATE_LIMIT_KEY_PREFIX",
    "REDIS_URL",
    "MAX_TOOL_OUTPUT_CHARS",
    "READ_FILE_MAX_BYTES",
    "WRITE_FILE_MAX_BYTES",
    "LOG_LEVEL",
    "METRICS_PORT",
)


@dataclass(frozen=True)
class DiscoveredTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    # MCP ToolAnnotations (readOnlyHint, …) as advertised by the server —
    # camelCase wire keys, additive/optional for older servers.
    annotations: dict[str, Any] | None = None
    # Published outputSchema for structuredContent, when advertised.
    output_schema: dict[str, Any] | None = None


FailureKind = Literal["timeout", "unknown_tool", "malformed", "server_error"]


@dataclass(frozen=True)
class ToolOutcome:
    """Normalized outcome of one ``tools/call`` — never throws."""

    ok: bool
    text: str
    duration_ms: int
    failure: FailureKind | None = None


class ToolInvoker(Protocol):
    """Minimal invoker surface — the agent loop depends on this, not the SDK."""

    def list_tools(self) -> list[DiscoveredTool]: ...

    async def call(self, name: str, args: dict[str, Any], request_id: str) -> ToolOutcome: ...


_UNKNOWN_TOOL_RE = re.compile(r"unknown tool|tool .+ not found|-3260[12]", re.IGNORECASE)


def server_env() -> dict[str, str]:
    """Build the child-process env: SDK defaults + the allow-list only."""
    env = get_default_environment()
    for key in SERVER_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


class McpAgentClient:
    """Connected MCP client over stdio. Use :func:`connect_stdio`."""

    def __init__(self, session: ClientSession, call_timeout_s: float = CALL_TIMEOUT_S):
        self._session = session
        self._call_timeout_s = call_timeout_s
        self._tools: list[DiscoveredTool] = []

    @classmethod
    @asynccontextmanager
    async def connect_stdio(
        cls,
        command: str,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        call_timeout_s: float = CALL_TIMEOUT_S,
        errlog: Any = None,
    ) -> AsyncIterator[McpAgentClient]:
        """Spawn the server subprocess, initialize the session, discover
        tools, and clean everything up on exit."""
        import sys

        params = StdioServerParameters(
            command=command,
            args=args,
            env=env if env is not None else server_env(),
        )
        async with (
            stdio_client(params, errlog=errlog or sys.stderr) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            client = cls(session, call_timeout_s)
            await client._discover()
            yield client

    @classmethod
    @asynccontextmanager
    async def connect_streamable_http(
        cls,
        url: str,
        *,
        token: str | None = None,
        call_timeout_s: float = CALL_TIMEOUT_S,
    ) -> AsyncIterator[McpAgentClient]:
        """Connect over Streamable HTTP — the opt-in remote transport.
        ``token`` becomes an ``Authorization: Bearer`` header; the server
        side decides whether to demand it (always on non-loopback)."""
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        headers = {"Authorization": f"Bearer {token}"} if token else None
        async with (
            httpx2.AsyncClient(headers=headers) as http_client,
            streamable_http_client(url, http_client=http_client) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            client = cls(session, call_timeout_s)
            await client._discover()
            yield client

    async def _discover(self) -> None:
        listed = await self._session.list_tools()
        self._tools = [
            DiscoveredTool(
                name=t.name,
                description=t.description or "",
                input_schema=dict(t.input_schema) if t.input_schema else {},
                annotations=(
                    t.annotations.model_dump(by_alias=True, exclude_none=True)
                    if t.annotations
                    else None
                ),
                output_schema=dict(t.output_schema) if t.output_schema else None,
            )
            for t in listed.tools
        ]

    def list_tools(self) -> list[DiscoveredTool]:
        return self._tools

    def to_openai_tools(self) -> list[OpenAIToolDef]:
        """Convert discovered MCP tools into OpenAI function-calling defs."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in self._tools
        ]

    async def call(
        self, name: str, args: dict[str, Any], request_id: str | None = None
    ) -> ToolOutcome:
        """Invoke a tool over the real transport. ``request_id`` rides in
        ``params._meta`` so server-side logs correlate with this client's
        events. Never raises — failures are typed outcomes."""
        started = time.perf_counter()

        def duration_ms() -> int:
            return round((time.perf_counter() - started) * 1000)

        meta: RequestParamsMeta = {
            "requestId": request_id or str(uuid.uuid4())  # type: ignore[typeddict-unknown-key]
        }
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(name, args, meta=meta),
                timeout=self._call_timeout_s,
            )
            text = _extract_text(result)
            if result.is_error:
                return ToolOutcome(
                    ok=False,
                    text=text,
                    failure="unknown_tool" if _UNKNOWN_TOOL_RE.search(text) else "server_error",
                    duration_ms=duration_ms(),
                )
            if text == "":
                return ToolOutcome(
                    ok=False,
                    text="(empty tool response)",
                    failure="malformed",
                    duration_ms=duration_ms(),
                )
            return ToolOutcome(ok=True, text=text, duration_ms=duration_ms())
        except TimeoutError:
            return ToolOutcome(
                ok=False,
                text=f"tool call timed out after {self._call_timeout_s * 1000:.0f}ms",
                failure="timeout",
                duration_ms=duration_ms(),
            )
        except MCPError as exc:
            return ToolOutcome(
                ok=False,
                text=f"tool call failed: {exc.error.message}",
                failure="server_error",
                duration_ms=duration_ms(),
            )
        except Exception as exc:
            return ToolOutcome(
                ok=False,
                text=f"tool call failed: {exc}",
                failure="server_error",
                duration_ms=duration_ms(),
            )


def _extract_text(result: Any) -> str:
    content = getattr(result, "content", None)
    if not isinstance(content, list):
        return ""
    return "\n".join(block.text for block in content if getattr(block, "type", None) == "text")
