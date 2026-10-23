"""Assemble the MCP server: tool registry + guarded invocation chain.

Each tool gets a thin ``@server.tool`` registration (the SDK derives the
input JSON schema from the signature) delegating to
:func:`guarded_invoke`, which applies rate limiting, the result envelope,
output sanitization and structured logging.

Beyond ``tools/call`` the server publishes honest capability metadata:
- **annotations** on every tool (read-only, open-world, idempotence),
- **output schemas** — each tool's ``Annotated[CallToolResult, X]``
  return type advertises the envelope as ``structuredContent`` while the
  JSON text stays for backwards compatibility,
- **resources** — ``reports://schema`` and ``sandbox://index``, both
  read-only views of server-side state,
- one **prompt**, ``research_briefing`` — a user-message template that
  composes the tools into a common task.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.types import CallToolResult, ToolAnnotations
from pydantic import Field

from mcp_toolkit.config import Settings
from mcp_toolkit.observability.logging import configure_logging, get_logger
from mcp_toolkit.observability.metrics import Metrics, create_metrics
from mcp_toolkit.server.guardrails.rate_limiter import RateLimiter
from mcp_toolkit.server.invocation import InvocationDeps, guarded_invoke
from mcp_toolkit.server.search import SearchProvider, create_search_provider
from mcp_toolkit.server.tools import build_tools
from mcp_toolkit.server.tools.db_query import open_readonly
from mcp_toolkit.server.tools.outputs import (
    DbQueryOutput,
    ReadFileOutput,
    WebSearchOutput,
    WriteFileOutput,
)

# Annotation semantics mirror the console's label dictionary — both must
# tell the same story about what a tool can do.
_READONLY_LOCAL = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    # Re-reading the same file/row has no additional environmental effect.
    idempotent_hint=True,
    open_world_hint=False,
)
_OPEN_WORLD_SEARCH = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
_SANDBOXED_WRITE = ToolAnnotations(
    read_only_hint=False,
    # Sandbox-bound, but an existing target is overwritten — prior bytes
    # are destroyed even though nothing is deleted.
    destructive_hint=True,
    # Re-writing the same bytes to the same path converges to one state.
    idempotent_hint=True,
    open_world_hint=False,
)

_SANDBOX_INDEX_LIMIT = 200


@dataclass(frozen=True)
class BuiltServer:
    server: MCPServer
    session_id: str
    metrics: Metrics


def _read_schema(db_path: str) -> str:
    """Dump the fixture database's SQL schema via the read-only connection."""
    conn = open_readonly(db_path)
    try:
        rows = conn.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name"
        ).fetchall()
        return "\n\n".join(row[0] for row in rows) or "-- empty schema"
    finally:
        conn.close()


def _sandbox_index(root: Path) -> str:
    """List files under the sandbox, capped — newest names first never
    promised; a deterministic alphabetical view plus a truncation note."""
    entries = sorted(
        (p for p in root.rglob("*") if p.is_file()),
        key=lambda p: str(p.relative_to(root)),
    )
    capped = entries[:_SANDBOX_INDEX_LIMIT]
    lines = [f"{p.relative_to(root)}\t{p.stat().st_size} bytes" for p in capped]
    if len(entries) > _SANDBOX_INDEX_LIMIT:
        lines.append(f"… and {len(entries) - _SANDBOX_INDEX_LIMIT} more files (listing capped)")
    return "\n".join(lines) or "(sandbox is empty)"


def build_server(
    settings: Settings,
    provider: SearchProvider | None = None,
    metrics: Metrics | None = None,
) -> BuiltServer:
    configure_logging(settings.log_level)
    logger = get_logger("mcp_toolkit.server")
    # The server owns its sandbox root — the canonical-path check needs it
    # to exist before any write can be authorized inside it.
    settings.sandbox_root.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(settings.rate_limit_per_minute)
    session_id = str(uuid.uuid4())
    metrics = metrics if metrics is not None else create_metrics()
    tools = {t.name: t for t in build_tools(settings, provider or create_search_provider(settings))}
    deps = InvocationDeps(
        limiter=limiter,
        session_id=session_id,
        max_output_chars=settings.max_tool_output_chars,
        logger=logger,
        metrics=metrics,
    )

    server = MCPServer(name="mcp-agent-toolkit", version="0.1.0")

    @server.tool(
        name="web_search",
        title="Web search",
        description=tools["web_search"].description,
        annotations=_OPEN_WORLD_SEARCH,
    )
    async def _web_search(
        ctx: Context[Any, Any],
        query: Annotated[str, Field(min_length=1, max_length=400, description="Search query")],
        max_results: Annotated[
            int, Field(ge=1, le=10, description="Maximum number of results (1-10)")
        ] = 5,
    ) -> Annotated[CallToolResult, WebSearchOutput]:
        return await guarded_invoke(
            tools["web_search"],
            {"query": query, "max_results": max_results},
            ctx,
            deps,
        )

    @server.tool(
        name="db_query",
        title="Database query",
        description=tools["db_query"].description,
        annotations=_READONLY_LOCAL,
    )
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
    ) -> Annotated[CallToolResult, DbQueryOutput]:
        return await guarded_invoke(tools["db_query"], {"query": query}, ctx, deps)

    @server.tool(
        name="read_file",
        title="File read",
        description=tools["read_file"].description,
        annotations=_READONLY_LOCAL,
    )
    async def _read_file(
        ctx: Context[Any, Any],
        path: Annotated[
            str,
            Field(min_length=1, max_length=500, description="File path, e.g. notes/brief.md"),
        ],
    ) -> Annotated[CallToolResult, ReadFileOutput]:
        return await guarded_invoke(tools["read_file"], {"path": path}, ctx, deps)

    @server.tool(
        name="write_file",
        title="File write",
        description=tools["write_file"].description,
        annotations=_SANDBOXED_WRITE,
    )
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
    ) -> Annotated[CallToolResult, WriteFileOutput]:
        return await guarded_invoke(
            tools["write_file"], {"path": path, "content": content}, ctx, deps
        )

    # ── Resources (read-only views) ─────────────────────────────────────

    @server.resource(
        "reports://schema",
        name="reports_schema",
        title="Reports schema",
        description="SQL schema of the fixture reports database — read-only.",
        mime_type="text/plain",
    )
    async def _reports_schema() -> str:
        return await asyncio.to_thread(_read_schema, str(settings.db_path))

    @server.resource(
        "sandbox://index",
        name="sandbox_index",
        title="Sandbox index",
        description=(f"Listing of files inside the write sandbox (first {_SANDBOX_INDEX_LIMIT})."),
        mime_type="text/plain",
    )
    async def _sandbox_index_resource() -> str:
        return await asyncio.to_thread(_sandbox_index, settings.sandbox_root)

    # ── Prompts ─────────────────────────────────────────────────────────

    @server.prompt(
        name="research_briefing",
        title="Research briefing",
        description=(
            "Compose web search, the reports database and the file-write "
            "tool into a cited briefing saved inside the sandbox."
        ),
    )
    def _research_briefing(
        topic: Annotated[str, Field(min_length=1, max_length=200, description="Topic to research")],
    ) -> str:
        # The topic goes into a user message only — never into a system
        # prompt. The filename is slugged so an adversarial topic cannot
        # smuggle a path into the suggested write.
        slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:60] or "topic"
        return (
            f'Research "{topic}". Search the web for current information, '
            "query the reports database for anything related, then combine "
            "both into a short briefing with every claim cited to a URL or "
            f"report title. Save it to `reports/{slug}.md` using the "
            "file-write tool and reply with a one-paragraph summary."
        )

    return BuiltServer(server=server, session_id=session_id, metrics=metrics)
