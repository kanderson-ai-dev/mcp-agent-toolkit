"""``db_query`` — read-only SQL against the seeded SQLite fixture.

Defense in depth: the statement guard rejects anything that isn't a
single SELECT/WITH, and the connection itself is opened read-only
(``mode=ro`` URI). sqlite3 is synchronous — every call runs in a worker
thread so the asyncio loop never stalls (ADR-7).
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from collections.abc import Callable
from typing import Any

from mcp_toolkit.result import ToolError
from mcp_toolkit.server.guardrails.sql_guard import assert_read_only_select
from mcp_toolkit.server.tools.types import ToolDef

MAX_ROWS = 500

DESCRIPTION = (
    "Run a single read-only SELECT statement against the internal SQLite "
    "fixture database. Schema: table `reports` (id, title, topic, author, "
    "published_at, summary); `topic` values are English keywords such as "
    "security, protocols, reliability, observability, retrieval. "
    "Returns JSON rows. Only SELECT/WITH statements are accepted — "
    "everything else is rejected."
)


def open_readonly(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
    # Second wall inside the same connection: even a URI-string bug that
    # dropped mode=ro could no longer write.
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _cell(value: Any) -> Any:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _rows_to_json(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [{k: _cell(row[k]) for k in row.keys()} for row in rows]  # noqa: SIM118


def create_db_query_tool(
    db_path: str, connect: Callable[[str], sqlite3.Connection] = open_readonly
) -> ToolDef:
    connection: sqlite3.Connection | None = None

    def _run(query: str) -> list[dict[str, Any]]:
        nonlocal connection
        if connection is None:
            connection = connect(db_path)
        cursor = connection.execute(query)
        return _rows_to_json(cursor.fetchall())

    async def handler(query: str, **_ignored: Any) -> dict[str, Any]:
        assert_read_only_select(query)
        try:
            rows = await asyncio.to_thread(_run, query)
        except sqlite3.Error as exc:
            message = str(exc)
            if re.search(r"unable to open database", message, re.IGNORECASE):
                raise ToolError(
                    "not_found",
                    f"Database not found at {db_path} — run "
                    "`uv run python -m scripts.seed_db` first",
                ) from exc
            if re.search(r"syntax error|no such table|no such column", message, re.IGNORECASE):
                raise ToolError("invalid_input", f"SQLite rejected the query: {message}") from exc
            raise
        truncated = len(rows) > MAX_ROWS
        return {
            "rows": rows[:MAX_ROWS],
            "row_count": min(len(rows), MAX_ROWS),
            "truncated": truncated,
        }

    return ToolDef(name="db_query", description=DESCRIPTION, handler=handler)
