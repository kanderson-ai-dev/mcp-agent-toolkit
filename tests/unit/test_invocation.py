"""In-process tests for the guarded invocation chain and server
assembly — complements the stdio integration suite (whose subprocess
work isn't visible to coverage)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import structlog

from mcp_toolkit.config import Settings
from mcp_toolkit.observability.request_context import get_request_id
from mcp_toolkit.result import ToolError
from mcp_toolkit.server.app import build_server
from mcp_toolkit.server.guardrails.rate_limiter import RateLimiter
from mcp_toolkit.server.invocation import (
    InvocationDeps,
    extract_request_id,
    guarded_invoke,
)
from mcp_toolkit.server.search import StubSearchProvider
from mcp_toolkit.server.tools.types import ToolDef


def _deps(**overrides: Any) -> InvocationDeps:
    configure = structlog.get_logger("test")
    return InvocationDeps(
        limiter=overrides.get("limiter", RateLimiter(60)),
        session_id=overrides.get("session_id", "sess-test"),
        max_output_chars=overrides.get("max_output_chars", 12_000),
        logger=configure,
    )


def _tool(handler: Any, name: str = "t") -> ToolDef:
    return ToolDef(name=name, description="d", handler=handler)


class _FakeRequestCtx:
    def __init__(self, meta: Any = None, request_id: Any = None) -> None:
        self.meta = meta
        self.request_id = request_id


class _FakeCtx:
    def __init__(self, rc: _FakeRequestCtx | None) -> None:
        self._rc = rc

    @property
    def request_context(self) -> _FakeRequestCtx:
        if self._rc is None:
            raise ValueError("not available outside of a request")
        return self._rc


def _parse(res: Any) -> dict[str, Any]:
    return json.loads(res.content[0].text)


class TestExtractRequestId:
    def test_meta_request_id_wins(self) -> None:
        ctx = _FakeCtx(_FakeRequestCtx(meta={"requestId": "r-1"}, request_id="rpc-9"))
        assert extract_request_id(ctx) == "r-1"

    def test_falls_back_to_json_rpc_id(self) -> None:
        ctx = _FakeCtx(_FakeRequestCtx(meta=None, request_id="rpc-9"))
        assert extract_request_id(ctx) == "rpc-9"

    def test_none_ctx_generates_uuid(self) -> None:
        rid = extract_request_id(None)
        assert len(rid) == 36

    def test_no_request_context_generates_uuid(self) -> None:
        assert len(extract_request_id(_FakeCtx(None))) == 36


class TestGuardedInvoke:
    async def test_ok_envelope(self) -> None:
        async def handler(**_kw: Any) -> dict[str, Any]:
            return {"v": 1}

        res = await guarded_invoke(_tool(handler), {}, None, _deps())
        assert res.is_error is False
        assert _parse(res) == {"status": "ok", "data": {"v": 1}}

    async def test_tool_error_maps_code_and_iserror(self) -> None:
        async def handler(**_kw: Any) -> dict[str, Any]:
            raise ToolError("forbidden", "nope")

        res = await guarded_invoke(_tool(handler), {}, None, _deps())
        assert res.is_error is True
        assert _parse(res)["code"] == "forbidden"

    async def test_unexpected_error_maps_internal(self) -> None:
        async def handler(**_kw: Any) -> dict[str, Any]:
            raise RuntimeError("boom")

        res = await guarded_invoke(_tool(handler), {}, None, _deps())
        assert res.is_error is True
        assert _parse(res)["code"] == "internal"

    async def test_rate_limited(self) -> None:
        limiter = RateLimiter(1)
        limiter.check("t")
        limiter.record("t")

        async def handler(**_kw: Any) -> dict[str, Any]:
            return {}

        res = await guarded_invoke(_tool(handler), {}, None, _deps(limiter=limiter))
        assert _parse(res)["code"] == "rate_limited"

    async def test_output_sanitized_and_capped(self) -> None:
        async def handler(**_kw: Any) -> dict[str, Any]:
            return {"text": "x" * 5000 + "\x00"}

        res = await guarded_invoke(_tool(handler), {}, None, _deps(max_output_chars=200))
        text = res.content[0].text
        assert len(text) < 300
        assert "truncated" in text
        assert "\x00" not in text

    async def test_request_id_bound_during_call(self) -> None:
        seen: list[str | None] = []

        async def handler(ctx: Any = None, **_kw: Any) -> dict[str, Any]:
            seen.append(get_request_id())
            seen.append(ctx.request_id)
            return {}

        ctx = _FakeCtx(_FakeRequestCtx(meta={"requestId": "rid-7"}, request_id="x"))
        await guarded_invoke(_tool(handler), {}, ctx, _deps())
        assert seen == ["rid-7", "rid-7"]
        assert get_request_id() is None


class TestBuildServer:
    async def test_lists_and_calls_tools_in_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.seed_db import seed_demo_db

        monkeypatch.delenv("SANDBOX_ROOT", raising=False)
        monkeypatch.delenv("DB_PATH", raising=False)
        db = tmp_path / "demo.db"
        seed_demo_db(db)
        settings = Settings(
            sandbox_root=tmp_path / "sandbox",
            db_path=db,
            search_provider="none",
        )
        built = build_server(settings, provider=StubSearchProvider())

        listed = await built.server.list_tools()
        assert {t.name for t in listed} == {
            "web_search",
            "db_query",
            "read_file",
            "write_file",
        }

        res = await built.server.call_tool("web_search", {"query": "mcp"})
        assert res.is_error is False
        assert _parse(res)["data"]["provider"] == "none"

        res = await built.server.call_tool("write_file", {"path": "a.md", "content": "hi"})
        assert _parse(res)["status"] == "ok"

        res = await built.server.call_tool("db_query", {"query": "SELECT 1 AS one"})
        assert _parse(res)["data"]["row_count"] == 1

        res = await built.server.call_tool("read_file", {"path": "a.md"})
        assert _parse(res)["data"]["content"] == "hi"
