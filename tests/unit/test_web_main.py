"""``web/__main__`` coverage — the production wiring, exercised in-process.

``build_app``'s lifespan owns the real MCP stdio child, the registry and
every dependency the API needs; ``_run_ab`` builds the full eval stack
around that live connection. ``main()`` itself (``uvicorn.run``) is the
only intentionally-uncovered line — it is a process side effect.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest
from scripts.seed_db import seed_demo_db

from mcp_toolkit.client.mcp_client import McpAgentClient, server_env
from mcp_toolkit.client.server_spawn import resolve_server_spawn
from mcp_toolkit.prompts.loader import load_prompt
from mcp_toolkit.prompts.registry import PromptRegistry
from mcp_toolkit.web.__main__ import _run_ab, _utf8_streams, build_app
from mcp_toolkit.web.app import WebDeps
from tests.helpers import copy_promoted_baseline, make_settings


def _settings(tmp_path: Path):
    registry_root = copy_promoted_baseline(Path("prompts"), tmp_path / "prompts")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    shutil.copy(Path("data/sandbox/welcome.txt"), sandbox / "welcome.txt")
    db = tmp_path / "demo.db"
    seed_demo_db(db)
    return make_settings(
        llm_provider="stub",
        search_provider="none",
        prompt_registry_dir=str(registry_root),
        prompt_audit_path=str(tmp_path / "audit.jsonl"),
        prompt_ab_results_dir=str(tmp_path / "ab"),
        sandbox_root=sandbox,
        db_path=db,
        metrics_port=None,
        # The dataset's burst row (ad-12) would exhaust the shared limit;
        # the limiter itself is covered elsewhere.
        rate_limit_per_minute=100000,
    )


async def test_build_app_lifespan_wires_everything(tmp_path: Path) -> None:
    """The lifespan spawns the MCP child, discovers tools, resolves the
    promoted prompt and installs fully-typed deps — the exact production
    boot path, without uvicorn."""
    app = build_app(_settings(tmp_path))
    async with app.router.lifespan_context(app):
        deps: WebDeps = app.state.deps
        assert deps.llm.name == "stub"
        assert {t.name for t in deps.discovered_tools} == {
            "web_search",
            "db_query",
            "read_file",
            "write_file",
        }
        assert deps.agent_prompt.name == "agent_system"
        assert deps.prompt_api is not None
        assert deps.prompt_provider is not None
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            assert (await c.get("/api/health")).status_code == 200
            status = (await c.get("/api/status")).json()
            assert status["mode"] == "stub"
            assert status["toolCount"] == 4
            assert status["prompts"]["agentSystem"]["version"] == 1
            assert status["prompts"]["judgeRubric"]["version"] == 1


async def test_run_ab_offline_smoke(tmp_path: Path) -> None:
    """``_run_ab`` offline: builds the eval stack over a live MCP
    connection and runs the StubLLM gate — mechanics only."""
    settings = _settings(tmp_path)
    spawn = resolve_server_spawn(settings)
    registry = PromptRegistry(settings.prompt_registry_dir, audit_path=settings.prompt_audit_path)
    async with McpAgentClient.connect_stdio(spawn.command, spawn.args, env=server_env()) as mcp:
        tools = mcp.to_openai_tools()
        baseline = load_prompt("agent_system", "latest-promoted", registry=registry)
        result = await _run_ab(
            "qa-smoke",
            baseline,
            baseline,
            offline=True,
            progress=lambda _msg: None,
            settings=settings,
            registry=registry,
            mcp=mcp,
            tools=tools,
        )
    assert result.mode == "offline-smoke"
    assert len(result.baseline.entries) > 0
    assert result.baseline.aggregate.task_success_avg is None  # no judge offline
    assert not result.promotion_allowed


def test_utf8_streams_is_idempotent() -> None:
    _utf8_streams()
    _utf8_streams()


async def test_run_ab_offline_refuses_live_without_key(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    registry = PromptRegistry(settings.prompt_registry_dir, audit_path=settings.prompt_audit_path)
    spawn = resolve_server_spawn(settings)
    async with McpAgentClient.connect_stdio(spawn.command, spawn.args, env=server_env()) as mcp:
        baseline = load_prompt("agent_system", "latest-promoted", registry=registry)
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            await _run_ab(
                "qa-live-blocked",
                baseline,
                baseline,
                offline=False,
                progress=lambda _msg: None,
                settings=settings,
                registry=registry,
                mcp=mcp,
                tools=mcp.to_openai_tools(),
            )
