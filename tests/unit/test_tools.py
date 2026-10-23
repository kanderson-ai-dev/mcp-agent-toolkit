"""Per-tool unit tests — ported from the oracle's tool tests.

Each tool is exercised through its ToolDef.handler directly (no MCP
transport) with external deps stubbed/mocked: httpx for web_search,
a seeded fixture DB for db_query, temp dirs for the file tools.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from scripts.seed_db import REPORTS, seed_demo_db

from mcp_toolkit.result import ToolError
from mcp_toolkit.server.search import (
    DuckDuckGoProvider,
    StubSearchProvider,
    TavilyProvider,
    create_search_provider,
)
from mcp_toolkit.server.tools.db_query import create_db_query_tool
from mcp_toolkit.server.tools.read_file import create_read_file_tool
from mcp_toolkit.server.tools.types import ToolContext
from mcp_toolkit.server.tools.web_search import create_web_search_tool
from mcp_toolkit.server.tools.write_file import create_write_file_tool

CTX = ToolContext(request_id="test-req", session_id="test-session")


# ── web_search ──────────────────────────────────────────────────────


async def test_web_search_stub_provider_deterministic() -> None:
    tool = create_web_search_tool(StubSearchProvider())
    data = await tool.handler(query="mcp", ctx=CTX)
    assert data["provider"] == "none"
    assert data["query"] == "mcp"
    assert len(data["results"]) == 2
    assert 'Stub result for "mcp"' in data["results"][0]["title"]


@respx.mock
async def test_web_search_tavily_maps_results() -> None:
    respx.post("https://api.tavily.com/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"title": "T1", "url": "https://a", "content": "s1"},
                    {"title": "T2", "url": "https://b", "content": "s2"},
                ]
            },
        )
    )
    provider = TavilyProvider("test-key")
    hits = await provider.search("q", 2)
    assert hits == [
        {"title": "T1", "url": "https://a", "snippet": "s1"},
        {"title": "T2", "url": "https://b", "snippet": "s2"},
    ]


@respx.mock
async def test_web_search_tavily_http_error_is_upstream() -> None:
    respx.post("https://api.tavily.com/search").mock(return_value=httpx.Response(503))
    provider = TavilyProvider("test-key")
    with pytest.raises(ToolError) as exc:
        await provider.search("q", 5)
    assert exc.value.code == "upstream"
    assert "503" in str(exc.value)


@respx.mock
async def test_web_search_duckduckgo_parses_html() -> None:
    respx.get(url__startswith="https://html.duckduckgo.com/html/?q=").mock(
        return_value=httpx.Response(
            200,
            text="""
            <div class="result">
              <a class="result__a" href="/l/?uddg=https%3A%2F%2Freal.example%2Fpage">Title A</a>
              <a class="result__snippet">Snippet A</a>
            </div>
            <div class="result">
              <a class="result__a" href="https://direct.example/">Title B</a>
              <div class="result__snippet">Snippet B</div>
            </div>
            """,
        )
    )
    hits = await DuckDuckGoProvider().search("q", 5)
    assert hits[0] == {
        "title": "Title A",
        "url": "https://real.example/page",
        "snippet": "Snippet A",
    }
    assert hits[1]["url"] == "https://direct.example/"


def test_provider_selection_auto_without_key_is_duckduckgo(
    clean_env: pytest.MonkeyPatch,
) -> None:
    from mcp_toolkit.config import Settings

    s = Settings(search_provider="auto", search_api_key=None)
    assert create_search_provider(s).name == "duckduckgo"


def test_provider_selection_none_is_stub(clean_env: pytest.MonkeyPatch) -> None:
    from mcp_toolkit.config import Settings

    s = Settings(search_provider="none")
    assert create_search_provider(s).name == "none"


def test_provider_tavily_without_key_fails_loudly(
    clean_env: pytest.MonkeyPatch,
) -> None:
    from mcp_toolkit.config import Settings

    s = Settings(search_provider="tavily", search_api_key=None)
    with pytest.raises(ToolError) as exc:
        create_search_provider(s)
    assert exc.value.code == "invalid_input"


@respx.mock
async def test_web_search_duckduckgo_http_error_is_upstream() -> None:
    respx.get(url__startswith="https://html.duckduckgo.com").mock(return_value=httpx.Response(500))
    with pytest.raises(ToolError) as exc:
        await DuckDuckGoProvider().search("q", 5)
    assert exc.value.code == "upstream"


@respx.mock
async def test_web_search_duckduckgo_request_failure_is_upstream() -> None:
    respx.get(url__startswith="https://html.duckduckgo.com").mock(
        side_effect=httpx.ConnectError("boom")
    )
    with pytest.raises(ToolError) as exc:
        await DuckDuckGoProvider().search("q", 5)
    assert exc.value.code == "upstream"


@respx.mock
async def test_web_search_duckduckgo_skips_malformed_results() -> None:
    respx.get(url__startswith="https://html.duckduckgo.com").mock(
        return_value=httpx.Response(
            200,
            text="""
            <div class="result"><span>no anchor</span></div>
            <div class="result">
              <a class="result__a" href="/relative-no-http">relative</a>
            </div>
            <div class="result">
              <a class="result__a" href="https://good.example/">Good</a>
              <div class="result__snippet">ok</div>
            </div>
            """,
        )
    )
    hits = await DuckDuckGoProvider().search("q", 5)
    assert [h["url"] for h in hits] == ["https://good.example/"]


def test_provider_selection_auto_with_key_is_tavily(
    clean_env: pytest.MonkeyPatch,
) -> None:
    from pydantic import SecretStr

    from mcp_toolkit.config import Settings

    s = Settings(search_provider="auto", search_api_key=SecretStr("k"))
    assert create_search_provider(s).name == "tavily"


def test_provider_selection_explicit_duckduckgo(
    clean_env: pytest.MonkeyPatch,
) -> None:
    from mcp_toolkit.config import Settings

    s = Settings(search_provider="duckduckgo")
    assert create_search_provider(s).name == "duckduckgo"


# ── db_query ────────────────────────────────────────────────────────


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "demo.db"
    seed_demo_db(path)
    return path


async def test_db_query_returns_rows(db_path: Path) -> None:
    tool = create_db_query_tool(str(db_path))
    data = await tool.handler(query="SELECT id, title FROM reports ORDER BY id", ctx=CTX)
    assert data["row_count"] == len(REPORTS)
    assert data["truncated"] is False
    assert data["rows"][0]["title"] == REPORTS[0]["title"]


async def test_db_query_rejects_writes(db_path: Path) -> None:
    tool = create_db_query_tool(str(db_path))
    for sql in ("DROP TABLE reports", "DELETE FROM reports", "SELECT 1; DROP TABLE reports"):
        with pytest.raises(ToolError) as exc:
            await tool.handler(query=sql, ctx=CTX)
        assert exc.value.code == "forbidden"


async def test_db_query_missing_file_is_not_found(tmp_path: Path) -> None:
    tool = create_db_query_tool(str(tmp_path / "nope.db"))
    with pytest.raises(ToolError) as exc:
        await tool.handler(query="SELECT 1", ctx=CTX)
    assert exc.value.code == "not_found"


async def test_db_query_bad_sql_is_invalid_input(db_path: Path) -> None:
    tool = create_db_query_tool(str(db_path))
    with pytest.raises(ToolError) as exc:
        await tool.handler(query="SELECT * FROM nope_table", ctx=CTX)
    assert exc.value.code == "invalid_input"


async def test_db_query_readonly_connection_blocks_writes(db_path: Path) -> None:
    """Second defense layer: even if the guard were bypassed, sqlite
    itself refuses writes on the read-only connection."""
    # Sneaking a write past the guard is impossible, so verify the
    # connection layer itself instead.
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE x (a)")
    conn.close()


async def test_db_query_connection_is_query_only(db_path: Path) -> None:
    """The read-only connection promised in the docs carries both flags:
    ``mode=ro`` in the URI *and* ``PRAGMA query_only=ON`` — sqlite itself
    refuses writes even if one flag were lost."""
    from mcp_toolkit.server.tools.db_query import open_readonly

    conn = open_readonly(str(db_path))
    try:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE x (a)")
    finally:
        conn.close()


def test_query_only_alone_refuses_writes(db_path: Path) -> None:
    """Why the pragma exists on top of ``mode=ro``: a normal read-write
    connection with only ``query_only`` set still refuses writes while
    reads keep working — an independent second wall."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA query_only=ON")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE x (a)")
        assert conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == len(REPORTS)
    finally:
        conn.close()


async def test_db_query_reuses_connection(db_path: Path) -> None:
    tool = create_db_query_tool(str(db_path))
    await tool.handler(query="SELECT 1", ctx=CTX)
    data = await tool.handler(query="SELECT 2", ctx=CTX)
    assert data["row_count"] == 1


async def test_db_query_unexpected_sqlite_error_propagates(db_path: Path) -> None:
    def broken_connect(_path: str) -> sqlite3.Connection:
        class _Conn:
            def execute(self, _q: str) -> Any:
                raise sqlite3.Error("disk I/O error")

        return _Conn()  # type: ignore[return-value]

    tool = create_db_query_tool(str(db_path), connect=broken_connect)
    with pytest.raises(sqlite3.Error):
        await tool.handler(query="SELECT 1", ctx=CTX)


async def test_db_query_truncates_to_max_rows(db_path: Path) -> None:
    big = db_path.parent / "big.db"
    conn = sqlite3.connect(big)
    conn.execute("CREATE TABLE nums (n INTEGER)")
    conn.executemany("INSERT INTO nums VALUES (?)", [(i,) for i in range(600)])
    conn.commit()
    conn.close()
    tool = create_db_query_tool(str(big))
    data = await tool.handler(query="SELECT n FROM nums", ctx=CTX)
    assert data["truncated"] is True
    assert data["row_count"] == 500


# ── read_file / write_file ──────────────────────────────────────────


async def test_read_write_round_trip(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    write = create_write_file_tool(sandbox, 1024)
    read = create_read_file_tool(sandbox, 1024)
    out = await write.handler(path="notes/a.md", content="hello world", ctx=CTX)
    assert out["bytes_written"] == 11
    data = await read.handler(path="notes/a.md", ctx=CTX)
    assert data["content"] == "hello world"
    assert data["bytes"] == 11


async def test_read_file_missing_is_not_found(tmp_path: Path) -> None:
    read = create_read_file_tool(tmp_path, 1024)
    with pytest.raises(ToolError) as exc:
        await read.handler(path="nope.txt", ctx=CTX)
    assert exc.value.code == "not_found"


async def test_read_file_too_large(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("x" * 2048)
    read = create_read_file_tool(tmp_path, 1024)
    with pytest.raises(ToolError) as exc:
        await read.handler(path="big.txt", ctx=CTX)
    assert exc.value.code == "invalid_input"
    assert "2048" in str(exc.value)


async def test_read_file_directory_is_invalid_input(tmp_path: Path) -> None:
    (tmp_path / "adir").mkdir()
    read = create_read_file_tool(tmp_path, 1024)
    with pytest.raises(ToolError) as exc:
        await read.handler(path="adir", ctx=CTX)
    assert exc.value.code == "invalid_input"


async def test_write_file_content_cap(tmp_path: Path) -> None:
    write = create_write_file_tool(tmp_path, 10)
    with pytest.raises(ToolError) as exc:
        await write.handler(path="a.txt", content="x" * 11, ctx=CTX)
    assert exc.value.code == "invalid_input"
    assert not (tmp_path / "a.txt").exists()


async def test_file_tools_reject_traversal(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    read = create_read_file_tool(sandbox, 1024)
    write = create_write_file_tool(sandbox, 1024)
    with pytest.raises(ToolError, match="escapes the sandbox"):
        await read.handler(path="../secret.txt", ctx=CTX)
    with pytest.raises(ToolError, match="escapes the sandbox"):
        await write.handler(path="../evil.txt", content="x", ctx=CTX)
    with pytest.raises(ToolError):
        await read.handler(path=os.path.join("..", "..", "etc", "passwd"), ctx=CTX)


# ── MCP resources: reports://schema and sandbox://index bodies ─────────


def test_read_schema_dumps_db_ddl(tmp_path: Path) -> None:
    """reports://schema returns the fixture DB's CREATE statements."""
    from mcp_toolkit.server.app import _read_schema

    db = tmp_path / "demo.db"
    seed_demo_db(db)
    text = _read_schema(str(db))
    assert "CREATE TABLE" in text.upper()
    assert "reports" in text


def test_sandbox_index_lists_and_caps(tmp_path: Path) -> None:
    """sandbox://index lists sandbox-relative paths + sizes, capped."""
    from mcp_toolkit.server.app import _SANDBOX_INDEX_LIMIT, _sandbox_index

    (tmp_path / "a.txt").write_text("alpha")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.md").write_text("beta")
    text = _sandbox_index(tmp_path)
    assert "a.txt" in text and "nested/b.md" in text or "nested\\b.md" in text
    assert "bytes" in text

    # Over the cap → explicit truncation note, never a silent slice.
    many = tmp_path / "many"
    many.mkdir()
    for i in range(_SANDBOX_INDEX_LIMIT + 5):
        (many / f"f{i:03}.txt").write_text("x")
    capped = _sandbox_index(many)
    assert "listing capped" in capped


def test_sandbox_index_empty(tmp_path: Path) -> None:
    from mcp_toolkit.server.app import _sandbox_index

    assert _sandbox_index(tmp_path) == "(sandbox is empty)"
