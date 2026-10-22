"""Entrypoint coverage — ``mcp_toolkit.server.__main__.main``."""

from __future__ import annotations

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
