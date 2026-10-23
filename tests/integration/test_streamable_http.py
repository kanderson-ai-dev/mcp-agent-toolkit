"""Streamable HTTP transport tests — the opt-in remote surface.

Covers the loopback/token policy, the bearer middleware's 401s, and one
real end-to-end: uvicorn serves the ASGI app, the SDK client connects,
discovers the same four tools and calls one.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from scripts.seed_db import seed_demo_db

from mcp_toolkit.client.mcp_client import McpAgentClient
from mcp_toolkit.config import Settings
from mcp_toolkit.server.app import build_server
from mcp_toolkit.server.search import StubSearchProvider
from mcp_toolkit.server.streamable import (
    BearerOnly,
    build_http_app,
    is_loopback_host,
    require_http_token,
)


def _settings(tmp_path: Path) -> Settings:
    db = tmp_path / "demo.db"
    seed_demo_db(db)
    return Settings(
        sandbox_root=tmp_path / "sandbox",
        db_path=db,
        search_provider="none",
        log_level="error",
    )


class TestLoopbackPolicy:
    def test_loopback_detection(self) -> None:
        for host in ("127.0.0.1", "localhost", "::1", "[::1]"):
            assert is_loopback_host(host), host
        for host in ("0.0.0.0", "10.0.0.5", "example.com", "::"):  # noqa: S104
            assert not is_loopback_host(host), host

    def test_loopback_bind_needs_no_token(self) -> None:
        require_http_token("127.0.0.1", None)  # no exception

    def test_non_loopback_requires_token(self) -> None:
        with pytest.raises(ValueError, match="MCP_HTTP_TOKEN"):
            require_http_token("0.0.0.0", None)  # noqa: S104

    def test_non_loopback_with_token_passes(self) -> None:
        require_http_token("0.0.0.0", "t0ken")  # noqa: S104


class TestBearerMiddleware:
    async def test_unauthorized_requests_get_401(self) -> None:
        sent: list[dict[str, Any]] = []

        async def send(msg: dict[str, Any]) -> None:
            sent.append(msg)

        async def app(scope: Any, receive: Any, send: Any) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        wrapped = BearerOnly(app, "s3cr3t")
        scope = {"type": "http", "headers": []}
        await wrapped(scope, None, send)
        assert sent[0]["status"] == 401
        assert b"unauthorized" in sent[1]["body"]

    async def test_wrong_token_gets_401(self) -> None:
        sent: list[dict[str, Any]] = []

        async def send(msg: dict[str, Any]) -> None:
            sent.append(msg)

        async def app(scope: Any, receive: Any, send: Any) -> None:
            raise AssertionError("must not reach the app")

        wrapped = BearerOnly(app, "s3cr3t")
        scope = {"type": "http", "headers": [(b"authorization", b"Bearer wrong")]}
        await wrapped(scope, None, send)
        assert sent[0]["status"] == 401

    async def test_right_token_passes(self) -> None:
        sent: list[dict[str, Any]] = []

        async def send(msg: dict[str, Any]) -> None:
            sent.append(msg)

        async def app(scope: Any, receive: Any, send: Any) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        wrapped = BearerOnly(app, "s3cr3t")
        scope = {"type": "http", "headers": [(b"authorization", b"Bearer s3cr3t")]}
        await wrapped(scope, None, send)
        assert sent[0]["status"] == 200


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@asynccontextmanager
async def _serve(app: Any, port: int) -> AsyncIterator[str]:
    """Run uvicorn on a random loopback port for the test's duration."""
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started, "uvicorn did not start"
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        await task


class TestStreamableHttpEndToEnd:
    async def test_discover_and_call_over_http(self, tmp_path: Path) -> None:
        built = build_server(_settings(tmp_path), provider=StubSearchProvider())
        app = build_http_app(built.server, host="127.0.0.1", token=None)
        port = _free_port()
        async with _serve(app, port) as url, McpAgentClient.connect_streamable_http(url) as client:
            tools = {t.name: t for t in client.list_tools()}
            assert set(tools) == {"web_search", "db_query", "read_file", "write_file"}
            # Annotations and output schemas travel over HTTP too.
            assert tools["web_search"].annotations is not None
            assert tools["web_search"].annotations["openWorldHint"] is True
            assert tools["db_query"].output_schema is not None

            outcome = await client.call("db_query", {"query": "SELECT 1 AS one"}, "http-req")
            assert outcome.ok, outcome.text
            assert json.loads(outcome.text)["data"]["row_count"] == 1

    async def test_bearer_token_enforced_over_http(self, tmp_path: Path) -> None:
        built = build_server(_settings(tmp_path), provider=StubSearchProvider())
        app = build_http_app(built.server, host="127.0.0.1", token="tok-e2e")  # noqa: S106
        port = _free_port()
        async with _serve(app, port) as url:
            # No token → the middleware 401s the MCP handshake.
            import httpx2

            async with httpx2.AsyncClient() as raw:
                resp = await raw.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
                assert resp.status_code == 401

            # Right token → full session works.
            async with McpAgentClient.connect_streamable_http(
                url,
                token="tok-e2e",  # noqa: S106
            ) as client:
                outcome = await client.call("web_search", {"query": "mcp"}, "tok-req")
                assert outcome.ok, outcome.text
