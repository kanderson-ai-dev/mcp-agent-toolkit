"""Entrypoint coverage — ``mcp_toolkit.server.__main__.main``."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


def test_server_main_runs_stdio(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    import mcp_toolkit.server.__main__ as entry

    fake = MagicMock()
    fake.server.run_stdio_async.return_value = "coro"
    monkeypatch.setattr(entry, "build_server", lambda _s: fake)
    ran: list[Any] = []
    monkeypatch.setattr(entry.asyncio, "run", ran.append)
    entry.main()
    assert ran == ["coro"]


def test_server_main_dispatches_streamable_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """``MCP_TRANSPORT=streamable-http`` routes to uvicorn, not stdio."""
    import mcp_toolkit.server.__main__ as entry

    fake = MagicMock()
    monkeypatch.setattr(entry, "build_server", lambda _s: fake)

    settings = entry.Settings(
        sandbox_root=Path("./data/sandbox"),
        db_path=Path("./data/demo.db"),
        mcp_transport="streamable-http",
        mcp_http_host="127.0.0.1",
        mcp_http_port=9,
    )
    monkeypatch.setattr(entry, "get_settings", lambda: settings)

    served: list[Any] = []
    monkeypatch.setattr(entry, "_serve_streamable_http", lambda s, b: served.append((s, b)))
    entry.main()
    assert served == [(settings, fake)]


def test_streamable_http_requires_token_off_loopback() -> None:
    """Binding a remote host without MCP_HTTP_TOKEN fails before uvicorn runs."""
    from mcp_toolkit.server.streamable import build_http_app

    with pytest.raises(ValueError, match="MCP_HTTP_TOKEN"):
        build_http_app(MagicMock(), host="0.0.0.0", token=None)  # noqa: S104
