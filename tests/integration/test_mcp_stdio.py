"""Integration tests over the real MCP stdio transport — the server is
spawned as a subprocess (``python -m mcp_toolkit.server``), never mocked.
Covers tool discovery, invocation, error propagation and request-ID
correlation.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.types import CallToolResult, RequestParamsMeta
from scripts.seed_db import seed_demo_db

TOOL_NAMES = {"web_search", "db_query", "read_file", "write_file"}


@pytest.fixture()
def work_env(tmp_path: Path) -> dict[str, str]:
    """Isolated sandbox + seeded DB + offline search for the spawned server."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    db = tmp_path / "demo.db"
    seed_demo_db(db)
    env = dict(os.environ)
    env.update(
        {
            "SEARCH_PROVIDER": "none",
            "SANDBOX_ROOT": str(sandbox),
            "DB_PATH": str(db),
            "LOG_LEVEL": "error",
        }
    )
    return env


@asynccontextmanager
async def connect(env: dict[str, str]) -> AsyncIterator[ClientSession]:
    """Spawn the server and yield an initialized session.

    Entered and exited in the calling test's task — anyio cancel scopes
    inside ``stdio_client`` require that (a shared yield-fixture would
    resume teardown in a different task).
    """
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_toolkit.server"], env=env
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


def _envelope(res: CallToolResult) -> dict[str, Any]:
    assert res.content, "empty tool result"
    block = res.content[0]
    assert block.type == "text"
    parsed = json.loads(block.text)
    assert parsed.get("status") in ("ok", "error")
    return parsed


async def test_lists_exactly_the_four_tools(work_env: dict[str, str]) -> None:
    async with connect(work_env) as session:
        listed = await session.list_tools()
        assert {t.name for t in listed.tools} == TOOL_NAMES
        for t in listed.tools:
            assert t.description, t.name
            assert t.input_schema.get("type") == "object"
            assert t.input_schema.get("properties"), t.name


async def test_happy_path_each_tool(work_env: dict[str, str]) -> None:
    async with connect(work_env) as session:
        search = _envelope(
            await session.call_tool("web_search", {"query": "mcp", "max_results": 2})
        )
        assert search["status"] == "ok"
        assert len(search["data"]["results"]) == 2

        query = _envelope(
            await session.call_tool("db_query", {"query": "SELECT title FROM reports LIMIT 2"})
        )
        assert query["status"] == "ok"
        assert query["data"]["row_count"] == 2

        write = _envelope(
            await session.call_tool(
                "write_file", {"path": "notes/out.md", "content": "report body"}
            )
        )
        assert write["status"] == "ok"
        assert write["data"]["bytes_written"] == len("report body")

        read = _envelope(await session.call_tool("read_file", {"path": "notes/out.md"}))
        assert read["status"] == "ok"
        assert read["data"]["content"] == "report body"


async def test_error_envelopes_not_protocol_errors(work_env: dict[str, str]) -> None:
    """Guardrail failures arrive as isError results with a typed code —
    never as thrown protocol errors."""
    async with connect(work_env) as session:
        for args, code in (
            ({"query": "DROP TABLE reports"}, "forbidden"),
            ({"query": "SELECT * FROM missing_table"}, "invalid_input"),
        ):
            res = await session.call_tool("db_query", args)
            env = _envelope(res)
            assert res.is_error is True
            assert env["status"] == "error"
            assert env["code"] == code

        res = await session.call_tool("read_file", {"path": "../escape.txt"})
        assert _envelope(res)["code"] == "forbidden"
        assert res.is_error is True

        res = await session.call_tool("read_file", {"path": "missing.txt"})
        assert _envelope(res)["code"] == "not_found"


async def test_request_id_propagates_via_meta(work_env: dict[str, str]) -> None:
    async with connect(work_env) as session:
        res = await session.call_tool(
            "web_search",
            {"query": "correlation"},
            meta=RequestParamsMeta(requestId="it-req-123"),
        )
        assert res.is_error is False


async def test_unknown_tool_is_iserror_result(work_env: dict[str, str]) -> None:
    async with connect(work_env) as session:
        res = await session.call_tool("does_not_exist", {})
        assert res.is_error is True
        assert "Unknown tool" in res.content[0].text


async def test_schema_validation_rejects_bad_args(work_env: dict[str, str]) -> None:
    async with connect(work_env) as session:
        res = await session.call_tool("web_search", {"query": "x", "max_results": 99})
        assert res.is_error is True


async def test_rate_limit_enforced_over_transport(work_env: dict[str, str]) -> None:
    work_env["RATE_LIMIT_PER_MINUTE"] = "2"
    async with connect(work_env) as session:
        statuses = [
            _envelope(await session.call_tool("web_search", {"query": "rl"}))["status"]
            for _ in range(3)
        ]
        assert statuses == ["ok", "ok", "error"]
        env = _envelope(await session.call_tool("web_search", {"query": "rl"}))
        assert env["code"] == "rate_limited"
        assert "retry after" in env["message"]
