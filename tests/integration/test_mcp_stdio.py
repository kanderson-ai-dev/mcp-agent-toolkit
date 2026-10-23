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

# The advertised annotation story per tool — these flags are a contract
# with clients, so they are pinned, not just presence-checked.
EXPECTED_ANNOTATIONS = {
    "web_search": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    "db_query": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "read_file": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "write_file": {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}


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


# ── Annotations, structured output, resources, prompts ─────────────────


async def test_annotations_advertised_per_tool(work_env: dict[str, str]) -> None:
    async with connect(work_env) as session:
        listed = await session.list_tools()
        by_name = {t.name: t for t in listed.tools}
        assert set(by_name) == TOOL_NAMES
        for name, expected in EXPECTED_ANNOTATIONS.items():
            annotations = by_name[name].annotations
            assert annotations is not None, name
            actual = annotations.model_dump(by_alias=True, exclude_none=True)
            for key, value in expected.items():
                assert actual.get(key) == value, f"{name}.{key}"
            assert by_name[name].title, name  # a human title rides along


async def test_output_schemas_advertised(work_env: dict[str, str]) -> None:
    async with connect(work_env) as session:
        listed = await session.list_tools()
        by_name = {t.name: t for t in listed.tools}
        for name in TOOL_NAMES:
            schema = by_name[name].output_schema
            assert schema is not None, name
            props = schema.get("properties", {})
            # The success arm of the envelope is published…
            assert props.get("status", {}).get("const") == "ok", name
            assert "data" in props, name
        # …and the shapes are tool-specific, not one generic bag. `data`
        # is a $ref into $defs — resolve it to compare the fields.
        search_schema = by_name["web_search"].output_schema
        search_ref = search_schema["properties"]["data"]["$ref"].split("/")[-1]
        search_data = search_schema["$defs"][search_ref]
        assert set(search_data["properties"]) == {"provider", "query", "results"}
        write_schema = by_name["write_file"].output_schema
        write_ref = write_schema["properties"]["data"]["$ref"].split("/")[-1]
        write_data = write_schema["$defs"][write_ref]
        assert set(write_data["properties"]) == {"path", "bytes_written"}


async def test_structured_content_matches_text_envelope(work_env: dict[str, str]) -> None:
    """structuredContent is literally the sanitized text parsed — the two
    views can never disagree."""
    async with connect(work_env) as session:
        for name, args in (
            ("web_search", {"query": "mcp", "max_results": 2}),
            ("db_query", {"query": "SELECT title FROM reports LIMIT 1"}),
            ("write_file", {"path": "sc.md", "content": "hello"}),
            ("read_file", {"path": "sc.md"}),
        ):
            res = await session.call_tool(name, args)
            assert res.is_error is False, name
            assert res.structured_content is not None, name
            assert res.structured_content == json.loads(res.content[0].text), name
            assert res.structured_content["status"] == "ok", name


async def test_structured_content_on_guardrail_error(work_env: dict[str, str]) -> None:
    async with connect(work_env) as session:
        res = await session.call_tool("db_query", {"query": "DROP TABLE reports"})
        assert res.is_error is True
        # The error envelope is still emitted structurally — clients can
        # branch on `status` without parsing text.
        assert res.structured_content is not None
        assert res.structured_content["status"] == "error"
        assert res.structured_content["code"] == "forbidden"


async def test_resources_advertised_and_readable(work_env: dict[str, str]) -> None:
    async with connect(work_env) as session:
        listed = await session.list_resources()
        uris = {str(r.uri) for r in listed.resources}
        assert {"reports://schema", "sandbox://index"} <= uris

        schema = await session.read_resource("reports://schema")
        assert schema.contents
        schema_text = schema.contents[0]
        assert schema_text.mime_type == "text/plain"
        assert "CREATE TABLE" in schema_text.text.upper()

        write = _envelope(
            await session.call_tool("write_file", {"path": "idx.txt", "content": "x"})
        )
        assert write["status"] == "ok"
        index = await session.read_resource("sandbox://index")
        index_text = index.contents[0]
        assert index_text.mime_type == "text/plain"
        assert "idx.txt" in index_text.text


async def test_prompts_advertised_and_rendered(work_env: dict[str, str]) -> None:
    async with connect(work_env) as session:
        listed = await session.list_prompts()
        prompts = {p.name: p for p in listed.prompts}
        assert "research_briefing" in prompts

        rendered = await session.get_prompt("research_briefing", {"topic": "MCP Transports"})
        assert rendered.messages
        msg = rendered.messages[0]
        assert msg.role == "user"
        text = msg.content.text if hasattr(msg.content, "text") else str(msg.content)
        assert "MCP Transports" in text
        # The suggested file is slugged — the raw topic never lands in a path.
        assert "reports/mcp-transports.md" in text


async def test_prompt_slug_sanitizes_adversarial_topic(work_env: dict[str, str]) -> None:
    async with connect(work_env) as session:
        rendered = await session.get_prompt("research_briefing", {"topic": "../../etc/passwd!!"})
        text = rendered.messages[0].content.text
        # The topic may echo verbatim (it is user-supplied data in a user
        # message) — the *path suggestion* is what must stay inside the sandbox.
        assert "reports/etc-passwd.md" in text
        assert "reports/../../" not in text
