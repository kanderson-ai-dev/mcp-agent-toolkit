"""Assemble the MCP server: tool registry + guarded invocation chain.

Each tool gets a thin ``@server.tool`` registration (the SDK derives the
input JSON schema from the signature) delegating to
:func:`guarded_invoke`, which applies rate limiting, the result envelope,
output sanitization and structured logging.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.types import CallToolResult
from pydantic import Field

from mcp_toolkit.config import Settings
from mcp_toolkit.observability.logging import configure_logging, get_logger
from mcp_toolkit.server.guardrails.rate_limiter import RateLimiter
from mcp_toolkit.server.invocation import InvocationDeps, guarded_invoke
from mcp_toolkit.server.search import SearchProvider, create_search_provider
from mcp_toolkit.server.tools import build_tools


@dataclass(frozen=True)
class BuiltServer:
    server: MCPServer
    session_id: str


def build_server(settings: Settings, provider: SearchProvider | None = None) -> BuiltServer:
    configure_logging(settings.log_level)
    logger = get_logger("mcp_toolkit.server")
    # The server owns its sandbox root — the canonical-path check needs it
    # to exist before any write can be authorized inside it.
    settings.sandbox_root.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(settings.rate_limit_per_minute)
    session_id = str(uuid.uuid4())
    tools = {t.name: t for t in build_tools(settings, provider or create_search_provider(settings))}
    deps = InvocationDeps(
        limiter=limiter,
        session_id=session_id,
        max_output_chars=settings.max_tool_output_chars,
        logger=logger,
    )

    server = MCPServer(name="mcp-agent-toolkit", version="0.1.0")

    @server.tool(name="web_search", description=tools["web_search"].description)
    async def _web_search(
        ctx: Context[Any, Any],
        query: Annotated[str, Field(min_length=1, max_length=400, description="Search query")],
        max_results: Annotated[
            int, Field(ge=1, le=10, description="Maximum number of results (1-10)")
        ] = 5,
    ) -> CallToolResult:
        return await guarded_invoke(
            tools["web_search"],
            {"query": query, "max_results": max_results},
            ctx,
            deps,
        )

    @server.tool(name="db_query", description=tools["db_query"].description)
    async def _db_query(
        ctx: Context[Any, Any],
        query: Annotated[
            str,
            Field(
                min_length=1,
                max_length=4000,
                description="A single SELECT statement, e.g. SELECT title, author FROM reports",
            ),
        ],
    ) -> CallToolResult:
        return await guarded_invoke(tools["db_query"], {"query": query}, ctx, deps)

    @server.tool(name="read_file", description=tools["read_file"].description)
    async def _read_file(
        ctx: Context[Any, Any],
        path: Annotated[
            str,
            Field(min_length=1, max_length=500, description="File path, e.g. notes/brief.md"),
        ],
    ) -> CallToolResult:
        return await guarded_invoke(tools["read_file"], {"path": path}, ctx, deps)

    @server.tool(name="write_file", description=tools["write_file"].description)
    async def _write_file(
        ctx: Context[Any, Any],
        path: Annotated[
            str,
            Field(
                min_length=1,
                max_length=500,
                description="Destination path relative to the sandbox, e.g. reports/out.md",
            ),
        ],
        content: Annotated[str, Field(description="UTF-8 text to write")],
    ) -> CallToolResult:
        return await guarded_invoke(
            tools["write_file"], {"path": path, "content": content}, ctx, deps
        )

    return BuiltServer(server=server, session_id=session_id)
