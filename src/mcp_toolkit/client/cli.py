"""Agent CLI — ``mcp-toolkit-agent "question" [--json] [--stub]``.

Spawns the MCP server over stdio, discovers its tools, and answers the
question through the LLM tool-calling loop. Events stream to stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from typing import Any

from mcp_toolkit.client.agent_loop import AgentLoopDeps, run_agent
from mcp_toolkit.client.events import LlmMessageEvent, format_event
from mcp_toolkit.client.llm import create_llm
from mcp_toolkit.client.mcp_client import McpAgentClient, server_env
from mcp_toolkit.client.server_spawn import resolve_server_spawn
from mcp_toolkit.config import get_settings, load_env_file
from mcp_toolkit.observability.logging import configure_logging
from mcp_toolkit.observability.metrics import create_metrics

USAGE = """The agent spawns the MCP server over stdio, discovers its tools,
and answers the question through an LLM tool-calling loop."""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mcp-toolkit-agent",
        description=USAGE,
    )
    parser.add_argument("question", nargs="+", help="the question to research")
    parser.add_argument("--json", action="store_true", help="emit JSON events")
    parser.add_argument("--stub", action="store_true", help="force StubLLM")
    return parser.parse_args(argv)


async def _run(argv: list[str]) -> int:
    args = _parse_args(argv)
    prompt = " ".join(args.question).strip()
    if not prompt:
        print(USAGE)
        return 2

    load_env_file()
    settings = get_settings()
    # Client logs go to stderr — stdout stays reserved for the event
    # stream, so --json output remains machine-parseable.
    configure_logging(settings.log_level)
    if args.stub:
        settings = settings.model_copy(update={"llm_provider": "stub"})
    fmt = "json" if args.json else settings.output_format

    spawn = resolve_server_spawn(settings)

    def emit(event: Any) -> None:
        print(format_event(event, fmt))

    async with McpAgentClient.connect_stdio(spawn.command, spawn.args, env=server_env()) as mcp:
        tools = mcp.list_tools()
        llm = create_llm(settings)
        metrics = create_metrics()
        emit(
            LlmMessageEvent(
                iteration=0,
                content=(
                    f"connected — {len(tools)} tools discovered "
                    f"({', '.join(t.name for t in tools)}), llm={llm.name}"
                ),
            )
        )
        result = await run_agent(
            prompt,
            AgentLoopDeps(
                llm=llm,
                invoker=mcp,
                tools=mcp.to_openai_tools(),
                max_iterations=settings.max_tool_iterations,
                emit=emit,
                metrics=metrics,
            ),
        )
        return 0 if result.terminated_by == "answer" else 1


def _utf8_streams() -> None:
    """Windows consoles default to cp1252 — the pretty event format uses
    Unicode (→, ←, …), so reconfigure both streams rather than crash."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError, OSError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def main() -> None:
    _utf8_streams()
    try:
        code = asyncio.run(_run(sys.argv[1:]))
    except KeyboardInterrupt:
        code = 130
    except Exception as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
