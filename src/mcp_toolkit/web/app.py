"""FastAPI adapter over the agent loop — no agent logic lives here,
only the HTTP/SSE surface.

``POST /api/chat`` validates and stashes the question behind a one-time
``runId``; ``GET /api/chat/stream`` consumes it and streams the real
``AgentEvent`` union as named SSE events (heartbeat comments keep the
connection alive during long LLM calls). ``frontend/dist`` is served
statically when present, with an SPA fallback that never shadows
``/api/*``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from mcp_toolkit.client.agent_loop import AgentLoopDeps, run_agent
from mcp_toolkit.client.events import AgentEvent, ErrorEvent
from mcp_toolkit.client.llm.types import LLMClient, OpenAIToolDef
from mcp_toolkit.client.mcp_client import DiscoveredTool, ToolInvoker
from mcp_toolkit.observability.metrics import Metrics
from mcp_toolkit.prompts.loader import LoadedPrompt
from mcp_toolkit.web import sse
from mcp_toolkit.web.prompts_routes import PromptAPIDeps, create_prompt_router
from mcp_toolkit.web.run_store import RunStore

_BODY_LIMIT = 32 * 1024
_QUESTION_MIN = 3
_QUESTION_MAX = 2000


@dataclass(frozen=True)
class WebDeps:
    """Everything the HTTP surface needs — injectable for tests."""

    invoker: ToolInvoker
    tools: list[OpenAIToolDef]
    discovered_tools: list[DiscoveredTool]
    llm: LLMClient
    max_iterations: int
    agent_prompt: LoadedPrompt
    runs: RunStore = field(default_factory=RunStore)
    metrics: Metrics | None = None
    heartbeat_s: float = 15.0
    cors_origin: str | None = None
    static_dir: Path | None = None
    # Resolves the prompt fresh per run — hot-swap: a promoted version
    # takes effect on the next run while in-flight runs keep theirs.
    prompt_provider: Callable[[], LoadedPrompt] | None = None
    prompt_api: PromptAPIDeps | None = None
    # Run telemetry: cost cap + resolved model price (None ⇒ cost hidden,
    # budget gate unevaluable).
    budget_usd: float | None = None
    price_per_mtok: tuple[float, float] | None = None
    # Live scorecard artifact for GET /api/status — read lazily per
    # request so a freshly committed scorecard shows up without restart.
    # Absent in deployments that do not ship evaluation/.
    scorecard_path: Path = Path("evaluation/scorecard.json")


def _deps(request: Request) -> WebDeps:
    deps: WebDeps = request.app.state.deps
    return deps


def _last_evaluation(path: Path) -> dict[str, Any] | None:
    """Read the committed scorecard's public fields; any unreadable or
    malformed artifact degrades to ``None`` rather than an error."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        aggregate = data["aggregate"]
        return {
            "ranAt": data["ranAt"],
            "passed": bool(aggregate["passed"]),
            "mode": data.get("mode"),
        }
    except (OSError, KeyError, TypeError, ValueError):
        return None


def create_app(
    deps: WebDeps | None = None,
    *,
    lifespan: Callable[[FastAPI], Any] | None = None,
) -> FastAPI:
    """Build the API app. ``deps`` may be injected directly (tests) or
    installed by a lifespan (production, which owns the MCP child)."""
    app = FastAPI(title="mcp-agent-toolkit", lifespan=lifespan)
    if deps is not None:
        app.state.deps = deps

    if deps is not None and deps.cors_origin:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[deps.cors_origin],
            allow_methods=["GET", "POST"],
            allow_headers=["content-type"],
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/status")
    async def status(request: Request) -> dict[str, Any]:
        """Public-safe deployment summary: provider mode, tool count,
        active prompt versions and the last committed evaluation's
        pass/fail — no scores, paths or model internals."""
        d = _deps(request)
        prompts: dict[str, Any] = {}
        provider = d.prompt_provider
        if provider is not None:
            prompts["agentSystem"] = {"version": provider().version}
        else:
            prompts["agentSystem"] = {"version": d.agent_prompt.version}
        registry = d.prompt_api.registry if d.prompt_api is not None else None
        if registry is not None:
            judge = registry.get_promoted("judge_rubric")
            prompts["judgeRubric"] = {"version": judge.version}
        return {
            "mode": d.llm.name,
            "toolCount": len(d.discovered_tools),
            "prompts": prompts,
            "lastEvaluation": _last_evaluation(d.scorecard_path),
        }

    @app.get("/api/tools")
    async def tools(request: Request) -> dict[str, Any]:
        d = _deps(request)
        return {
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "annotations": t.annotations,
                    "outputSchema": t.output_schema,
                }
                for t in d.discovered_tools
            ],
            "llm": {"provider": d.llm.name},
        }

    @app.post("/api/chat")
    async def chat(request: Request) -> JSONResponse:
        body = await request.body()
        if len(body) > _BODY_LIMIT:
            return JSONResponse({"error": "Request body too large."}, status_code=413)
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        question = payload.get("question") if isinstance(payload, dict) else None
        if not isinstance(question, str):
            return JSONResponse({"error": "Invalid request."}, status_code=400)
        question = question.strip()
        if len(question) < _QUESTION_MIN:
            return JSONResponse(
                {"error": "Question must be at least 3 characters."}, status_code=400
            )
        if len(question) > _QUESTION_MAX:
            return JSONResponse(
                {"error": "Question must be at most 2000 characters."}, status_code=400
            )
        run_id = _deps(request).runs.create(question)
        return JSONResponse({"runId": run_id}, status_code=201)

    @app.get("/api/chat/stream")
    async def stream(request: Request, runId: str | None = None) -> StreamingResponse:  # noqa: N803
        d = _deps(request)
        question = d.runs.consume(runId) if runId else None
        if question is None:
            return JSONResponse(
                {"error": "Unknown or expired run. Submit a new question first."},
                status_code=404,
            )  # type: ignore[return-value]
        return StreamingResponse(
            _event_stream(question, d, request),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # Always mounted — deps are resolved per-request from app.state so
    # the router also works when a lifespan installs them later.
    def _prompt_api() -> PromptAPIDeps | None:
        d = getattr(app.state, "deps", None)
        return d.prompt_api if d is not None else None

    app.include_router(create_prompt_router(_prompt_api))

    _mount_static(app, deps)

    return app


async def _is_disconnected(request: Request) -> bool:
    result: bool | Awaitable[bool] = request.is_disconnected()
    if inspect.isawaitable(result):
        return await result
    return result


async def _event_stream(question: str, deps: WebDeps, request: Request) -> AsyncIterator[bytes]:
    """Run the agent for ``question``, yielding SSE frames as events
    arrive. The agent task is cancelled (never orphaned) when the client
    disconnects mid-stream; LLM/loop failures surface as an ``error``
    event, matching the oracle."""
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

    async def _run() -> None:
        try:
            prompt = deps.prompt_provider() if deps.prompt_provider else deps.agent_prompt
            await run_agent(
                question,
                AgentLoopDeps(
                    llm=deps.llm,
                    invoker=deps.invoker,
                    tools=deps.tools,
                    max_iterations=deps.max_iterations,
                    emit=queue.put_nowait,
                    prompt=prompt,
                    metrics=deps.metrics,
                    budget_usd=deps.budget_usd,
                    price_per_mtok=deps.price_per_mtok,
                ),
            )
        except Exception as exc:
            queue.put_nowait(ErrorEvent(message=str(exc)))
        finally:
            queue.put_nowait(None)  # stream terminator

    task = asyncio.create_task(_run())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=deps.heartbeat_s)
            except TimeoutError:
                if await _is_disconnected(request):
                    break
                yield sse.HEARTBEAT
                continue
            if item is None:
                return
            yield sse.frame(item)
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def _mount_static(app: FastAPI, deps: WebDeps | None) -> None:
    """Serve ``frontend/dist`` plus an SPA fallback. Registered last —
    explicit ``/api/*`` routes always win, and the catch-all explicitly
    refuses ``api/`` paths so a bad API route can never fall through to
    index.html."""
    static_dir = (
        deps.static_dir
        if deps is not None and deps.static_dir is not None
        else Path(__file__).resolve().parents[3] / "frontend" / "dist"
    )
    if not static_dir.is_dir():
        return
    root = static_dir.resolve()

    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> Response:
        if full_path.startswith("api/") or full_path == "api":
            return JSONResponse({"error": "Not found."}, status_code=404)
        candidate = (root / full_path).resolve() if full_path else root
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(root / "index.html")
