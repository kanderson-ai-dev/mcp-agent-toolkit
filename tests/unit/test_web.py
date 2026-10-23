"""Web adapter tests — httpx against the ASGI app, agent doubles only.

Covers the status-code matrix, validation messages, event→SSE mapping,
single-run consumption, heartbeat, disconnect cleanup, security headers,
CORS, and the static mount that must never shadow ``/api/*``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from mcp_toolkit.client.events import ToolCallEvent
from mcp_toolkit.client.llm.types import LLMResponse, OpenAIToolDef, ToolCallDef
from mcp_toolkit.client.mcp_client import DiscoveredTool, ToolOutcome
from mcp_toolkit.web.app import WebDeps, create_app
from mcp_toolkit.web.run_store import RunStore
from mcp_toolkit.web.sse import frame
from tests.helpers import test_prompt

TOOLS = [
    DiscoveredTool(
        name="web_search",
        description="Search the web",
        input_schema={},
        annotations={"readOnlyHint": True, "openWorldHint": True},
        output_schema={"type": "object", "properties": {"status": {"const": "ok"}}},
    ),
    DiscoveredTool(name="db_query", description="Query reports", input_schema={}),
]


class ScriptedLLM:
    name = "scripted"
    model = "scripted-model"

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)

    async def complete(self, _m: Any, _t: list[OpenAIToolDef]) -> LLMResponse:
        return self.responses.pop(0)


class HangingLLM:
    """LLM that never answers — used to prove mid-stream disconnect cancels
    the agent task instead of orphaning it."""

    name = "hanging"
    model = "hanging-model"

    def __init__(self) -> None:
        self.cancelled = False

    async def complete(self, _m: Any, _t: list[OpenAIToolDef]) -> LLMResponse:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("unreachable")


class FakeInvoker:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def list_tools(self) -> list[DiscoveredTool]:
        return TOOLS

    async def call(self, name: str, _a: dict[str, Any], _rid: str) -> ToolOutcome:
        self.calls.append(name)
        return ToolOutcome(ok=True, text='{"status":"ok","data":{}}', duration_ms=3)


def _deps(llm: Any, **overrides: Any) -> WebDeps:
    return WebDeps(
        invoker=overrides.get("invoker", FakeInvoker()),
        tools=[],
        discovered_tools=TOOLS,
        llm=llm,
        max_iterations=4,
        agent_prompt=test_prompt(),
        runs=overrides.get("runs", RunStore()),
        heartbeat_s=overrides.get("heartbeat_s", 15.0),
        cors_origin=overrides.get("cors_origin"),
        static_dir=overrides.get("static_dir"),
    )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestEndpoints:
    async def test_health(self) -> None:
        async with _client(create_app(_deps(ScriptedLLM([])))) as c:
            res = await c.get("/api/health")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

    async def test_tools_lists_discovered_and_llm(self) -> None:
        async with _client(create_app(_deps(ScriptedLLM([])))) as c:
            res = await c.get("/api/tools")
        body = res.json()
        assert body["llm"] == {"provider": "scripted"}
        assert {t["name"] for t in body["tools"]} == {"web_search", "db_query"}

    async def test_tools_surfaces_mcp_metadata(self) -> None:
        """Annotations + outputSchema are additive fields on /api/tools —
        absent keys degrade to null for older servers."""
        async with _client(create_app(_deps(ScriptedLLM([])))) as c:
            res = await c.get("/api/tools")
        tools = {t["name"]: t for t in res.json()["tools"]}
        assert tools["web_search"]["annotations"] == {
            "readOnlyHint": True,
            "openWorldHint": True,
        }
        assert tools["web_search"]["outputSchema"]["type"] == "object"
        assert tools["db_query"]["annotations"] is None
        assert tools["db_query"]["outputSchema"] is None

    async def test_post_chat_returns_runid_201(self) -> None:
        async with _client(create_app(_deps(ScriptedLLM([])))) as c:
            res = await c.post("/api/chat", json={"question": "What reports exist?"})
        assert res.status_code == 201
        assert isinstance(res.json()["runId"], str)

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ({"question": "ab"}, "Question must be at least 3 characters."),
            ({"question": "   "}, "Question must be at least 3 characters."),
            ({"question": "x" * 2001}, "Question must be at most 2000 characters."),
            ({"question": 42}, "Invalid request."),
            ({}, "Invalid request."),
            ("not a dict", "Invalid request."),
        ],
    )
    async def test_post_chat_validation_matrix(self, payload: Any, expected: str) -> None:
        async with _client(create_app(_deps(ScriptedLLM([])))) as c:
            res = await c.post("/api/chat", json=payload)
        assert res.status_code == 400
        assert res.json() == {"error": expected}

    async def test_post_chat_trims_before_validating(self) -> None:
        async with _client(create_app(_deps(ScriptedLLM([])))) as c:
            res = await c.post("/api/chat", json={"question": "  valid question?  "})
        assert res.status_code == 201

    async def test_body_limit_413(self) -> None:
        big = {"question": "q" * 33 * 1024}
        async with _client(create_app(_deps(ScriptedLLM([])))) as c:
            res = await c.post(
                "/api/chat",
                content=json.dumps(big).encode(),
                headers={"content-type": "application/json"},
            )
        assert res.status_code == 413
        assert "error" in res.json()

    async def test_stream_unknown_run_404(self) -> None:
        async with _client(create_app(_deps(ScriptedLLM([])))) as c:
            res = await c.get("/api/chat/stream", params={"runId": "nope"})
        assert res.status_code == 404
        assert "expired" in res.json()["error"]

    async def test_security_headers_and_no_powered_by(self) -> None:
        async with _client(create_app(_deps(ScriptedLLM([])))) as c:
            res = await c.get("/api/health")
        assert res.headers["X-Content-Type-Options"] == "nosniff"
        assert res.headers["Referrer-Policy"] == "no-referrer"
        assert res.headers["Cache-Control"] == "no-store"
        assert "x-powered-by" not in {k.lower() for k in res.headers}

    async def test_cors_only_for_configured_origin(self) -> None:
        app = create_app(_deps(ScriptedLLM([]), cors_origin="http://localhost:5173"))
        async with _client(app) as c:
            res = await c.get("/api/health", headers={"origin": "http://localhost:5173"})
            assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"
            res = await c.get("/api/health", headers={"origin": "https://evil.example"})
            assert "access-control-allow-origin" not in res.headers


class TestStream:
    async def test_sse_event_sequence(self) -> None:
        llm = ScriptedLLM(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[ToolCallDef(id="1", name="web_search", arguments='{"query":"q"}')],
                ),
                LLMResponse(content="final answer"),
            ]
        )
        async with _client(create_app(_deps(llm))) as c:
            run = await c.post("/api/chat", json={"question": "research mcp"})
            run_id = run.json()["runId"]
            res = await c.get("/api/chat/stream", params={"runId": run_id})

        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        body = res.text
        names = [
            line.removeprefix("event: ") for line in body.splitlines() if line.startswith("event: ")
        ]
        assert names == ["tool_call", "tool_result", "final"]
        # camelCase wire keys, matching the oracle fixture shape.
        data = json.loads(
            next(
                line.removeprefix("data: ")
                for line in body.splitlines()
                if line.startswith("data: ")
            )
        )
        assert "requestId" in data and "request_id" not in data

    async def test_run_is_single_use(self) -> None:
        llm = ScriptedLLM([LLMResponse(content="x")])
        async with _client(create_app(_deps(llm))) as c:
            run = await c.post("/api/chat", json={"question": "again"})
            run_id = run.json()["runId"]
            assert (await c.get("/api/chat/stream", params={"runId": run_id})).status_code == 200
            second = await c.get("/api/chat/stream", params={"runId": run_id})
        assert second.status_code == 404

    async def test_agent_error_becomes_error_event(self) -> None:
        class BoomLLM:
            name = "boom"
            model = "boom-model"

            async def complete(self, _m: Any, _t: Any) -> LLMResponse:
                raise RuntimeError("provider down")

        async with _client(create_app(_deps(BoomLLM()))) as c:
            run = await c.post("/api/chat", json={"question": "will fail"})
            res = await c.get("/api/chat/stream", params={"runId": run.json()["runId"]})
        assert "event: error" in res.text
        assert "provider down" in res.text

    async def test_disconnect_cancels_agent_task(self) -> None:
        from mcp_toolkit.web.app import _event_stream

        llm = HangingLLM()
        deps = _deps(llm, heartbeat_s=0.02)

        class FakeRequest:
            async def is_disconnected(self) -> bool:
                return False

        gen = _event_stream("q", deps, FakeRequest())  # type: ignore[arg-type]
        # First frame starts the agent task (an async generator's body
        # only runs once pulled); heartbeat arrives quickly.
        assert await asyncio.wait_for(gen.__anext__(), timeout=2) == b": heartbeat\n\n"
        await gen.aclose()
        assert llm.cancelled is True

    async def test_heartbeat_frames(self) -> None:
        from mcp_toolkit.web.app import _event_stream

        llm = HangingLLM()
        deps = _deps(llm, heartbeat_s=0.02)

        class FakeRequest:
            async def is_disconnected(self) -> bool:
                return False

        gen = _event_stream("q", deps, FakeRequest())  # type: ignore[arg-type]
        frame_bytes = await asyncio.wait_for(gen.__anext__(), timeout=2)
        await gen.aclose()
        assert frame_bytes == b": heartbeat\n\n"


class TestRunStore:
    def test_create_consume_single_use(self) -> None:
        store = RunStore()
        rid = store.create("q")
        assert store.consume(rid) == "q"
        assert store.consume(rid) is None

    def test_ttl_expiry(self) -> None:
        clock = [1000.0]
        store = RunStore(ttl_s=60, now=lambda: clock[0])
        rid = store.create("q")
        clock[0] += 61
        assert store.consume(rid) is None

    def test_max_pending_evicts_oldest(self) -> None:
        store = RunStore(max_pending=2)
        store.create("a")
        store.create("b")
        store.create("c")
        assert store.size == 2


class TestStaticMount:
    async def test_spa_fallback_and_api_not_shadowed(self, tmp_path: Path) -> None:
        dist = tmp_path / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<html>console</html>")
        (dist / "assets" / "app.js").write_text("js")
        deps = _deps(ScriptedLLM([]), static_dir=dist)
        async with _client(create_app(deps)) as c:
            index = await c.get("/")
            deep = await c.get("/some/spa/route")
            asset = await c.get("/assets/app.js")
            api_404 = await c.get("/api/nonexistent")
            api_shadow = await c.get("/api")
        assert index.status_code == 200 and "console" in index.text
        assert deep.status_code == 200 and "console" in deep.text
        assert asset.status_code == 200
        assert api_404.status_code == 404
        assert "console" not in api_404.text
        assert api_shadow.status_code == 404

    async def test_no_static_dir_skips_mount(self, tmp_path: Path) -> None:
        deps = _deps(ScriptedLLM([]), static_dir=tmp_path / "missing")
        async with _client(create_app(deps)) as c:
            assert (await c.get("/")).status_code == 404


class TestSseFrame:
    def test_frame_is_named_event_with_camel_json(self) -> None:
        out = frame(ToolCallEvent(iteration=1, request_id="r", tool="t", args={"a": 1}))
        assert out.startswith(b"event: tool_call\n")
        data = json.loads(out.decode().split("data: ")[1])
        assert data["requestId"] == "r"
