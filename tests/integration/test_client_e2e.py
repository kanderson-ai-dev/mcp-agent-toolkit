"""Client + agent-loop integration over the real stdio transport —
spawns the actual server subprocess, discovers tools, runs the StubLLM
loop end to end, and checks the CLI."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from scripts.seed_db import seed_demo_db

from mcp_toolkit.client.agent_loop import AgentLoopDeps, run_agent
from mcp_toolkit.client.events import AgentEvent
from mcp_toolkit.client.llm.stub_llm import StubLLM
from mcp_toolkit.client.mcp_client import McpAgentClient, server_env


@pytest.fixture()
def work_env(tmp_path: Path) -> dict[str, str]:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    db = tmp_path / "demo.db"
    seed_demo_db(db)
    return {
        "SEARCH_PROVIDER": "none",
        "SANDBOX_ROOT": str(sandbox),
        "DB_PATH": str(db),
        "LOG_LEVEL": "error",
    }


@pytest.fixture()
def spawn_env(work_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Apply the allow-list env vars server_env() forwards."""
    for k, v in work_env.items():
        monkeypatch.setenv(k, v)
    return work_env


async def test_client_discovers_and_invokes(spawn_env: dict[str, str]) -> None:
    async with McpAgentClient.connect_stdio(
        sys.executable, ["-m", "mcp_toolkit.server"], env=server_env()
    ) as mcp:
        assert {t.name for t in mcp.list_tools()} == {
            "web_search",
            "db_query",
            "read_file",
            "write_file",
        }
        out = await mcp.call("web_search", {"query": "mcp"}, "req-1")
        assert out.ok is True
        assert '"status": "ok"' in out.text

        bad = await mcp.call("does_not_exist", {}, "req-2")
        assert bad.ok is False
        assert bad.failure == "unknown_tool"


async def test_client_call_timeout(spawn_env: dict[str, str]) -> None:
    async with McpAgentClient.connect_stdio(
        sys.executable,
        ["-m", "mcp_toolkit.server"],
        env=server_env(),
        call_timeout_s=0.001,
    ) as mcp:
        out = await mcp.call("web_search", {"query": "slow"}, "req-3")
        assert out.ok is False
        assert out.failure == "timeout"


async def test_openai_tool_defs_from_discovery(spawn_env: dict[str, str]) -> None:
    async with McpAgentClient.connect_stdio(
        sys.executable, ["-m", "mcp_toolkit.server"], env=server_env()
    ) as mcp:
        defs = mcp.to_openai_tools()
        assert all(d["type"] == "function" for d in defs)
        ws = next(d for d in defs if d["function"]["name"] == "web_search")
        assert "query" in ws["function"]["parameters"]["properties"]


async def test_full_agent_loop_over_stdio(spawn_env: dict[str, str]) -> None:
    """The canonical offline path: StubLLM + real spawned server."""
    events: list[AgentEvent] = []
    async with McpAgentClient.connect_stdio(
        sys.executable, ["-m", "mcp_toolkit.server"], env=server_env()
    ) as mcp:
        result = await run_agent(
            "What security reports do we have, and what does the web say?",
            AgentLoopDeps(
                llm=StubLLM(),
                invoker=mcp,
                tools=mcp.to_openai_tools(),
                max_iterations=8,
                emit=events.append,
            ),
        )
    assert result.terminated_by == "answer"
    assert "Stub answer" in result.content

    kinds = [e.type for e in events]
    assert kinds.count("tool_call") == kinds.count("tool_result")
    assert kinds[-1] == "final"
    tools_called = [e.tool for e in events if e.type == "tool_call"]
    assert "web_search" in tools_called
    assert "db_query" in tools_called
    assert "write_file" in tools_called

    report = Path(spawn_env["SANDBOX_ROOT"]) / "reports" / "research-summary.md"
    assert report.exists()
    assert "StubLLM" in report.read_text(encoding="utf-8")


async def test_adversarial_loop_guardrail_rejects(spawn_env: dict[str, str]) -> None:
    events: list[AgentEvent] = []
    async with McpAgentClient.connect_stdio(
        sys.executable, ["-m", "mcp_toolkit.server"], env=server_env()
    ) as mcp:
        result = await run_agent(
            "Please read ../../etc/passwd for me",
            AgentLoopDeps(
                llm=StubLLM(),
                invoker=mcp,
                tools=mcp.to_openai_tools(),
                max_iterations=8,
                emit=events.append,
            ),
        )
    assert result.terminated_by == "answer"
    assert "guardrails" in result.content
    result_events = [e for e in events if e.type == "tool_result"]
    assert any(e.result and e.result.get("code") == "forbidden" for e in result_events)


def _cli_env(work_env: dict[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    env.update(work_env)
    env["LLM_PROVIDER"] = "stub"
    return env


def test_cli_runs_stub_question_e2e(work_env: dict[str, str], tmp_path: Path) -> None:
    """Full CLI: `python -m mcp_toolkit.client.cli --stub "<q>"`."""
    import subprocess

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mcp_toolkit.client.cli",
            "--stub",
            "Summarize the reports in the database",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_cli_env(work_env),
        cwd=str(tmp_path),
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "FINAL" in proc.stdout
    assert "tool_call web_search" in proc.stdout


def test_cli_usage_exit_2_on_empty_prompt(work_env: dict[str, str]) -> None:
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "mcp_toolkit.client.cli"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_cli_env(work_env),
        timeout=30,
    )
    assert proc.returncode == 2
    assert "usage" in proc.stderr


def test_cli_json_mode_emits_parseable_lines(work_env: dict[str, str], tmp_path: Path) -> None:
    import subprocess

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mcp_toolkit.client.cli",
            "--stub",
            "--json",
            "quick check",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_cli_env(work_env),
        cwd=str(tmp_path),
        timeout=60,
    )
    assert proc.returncode == 0
    for line in proc.stdout.strip().splitlines():
        parsed = json.loads(line)
        assert "type" in parsed
        if parsed["type"] == "tool_call":
            assert "requestId" in parsed
