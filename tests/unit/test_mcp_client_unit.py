"""Client unit tests — env allow-list and text extraction (transport
paths live in the integration suite)."""

from __future__ import annotations

import os
from typing import Any

import pytest

from mcp_toolkit.client.mcp_client import _extract_text, server_env
from mcp_toolkit.client.server_spawn import resolve_server_spawn
from mcp_toolkit.config import Settings


def test_server_env_allow_list_blocks_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("SEARCH_PROVIDER", "none")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "lsv2-should-not-leak")
    env = server_env()
    assert env.get("SEARCH_PROVIDER") == "none"
    assert "OPENAI_API_KEY" not in env
    assert "LANGCHAIN_API_KEY" not in env


def test_server_env_includes_os_basics(monkeypatch: pytest.MonkeyPatch) -> None:
    env = server_env()
    if os.name == "nt":
        assert "SystemRoot" in env or "SYSTEMROOT" in env
    assert "PATH" in env


def test_extract_text_joins_text_blocks() -> None:
    class B:
        def __init__(self, type_: str, text: str = "") -> None:
            self.type = type_
            self.text = text

    class R:
        def __init__(self, content: Any) -> None:
            self.content = content

    assert _extract_text(R([B("text", "a"), B("image"), B("text", "b")])) == "a\nb"
    assert _extract_text(R([])) == ""
    assert _extract_text(R(None)) == ""
    assert _extract_text(object()) == ""


def test_resolve_server_spawn_default_is_python_module(clean_env: pytest.MonkeyPatch) -> None:
    import sys

    spawn = resolve_server_spawn(Settings())
    assert spawn.command == sys.executable
    assert spawn.args == ["-m", "mcp_toolkit.server"]


def test_resolve_server_spawn_override(clean_env: pytest.MonkeyPatch) -> None:
    spawn = resolve_server_spawn(
        Settings(mcp_server_command="custom", mcp_server_args="--flag x  --y")
    )
    assert spawn.command == "custom"
    assert spawn.args == ["--flag", "x", "--y"]
