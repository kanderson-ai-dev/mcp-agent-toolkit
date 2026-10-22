"""Non-interactive end-to-end demo.

One question that exercises three tools — ``web_search`` and ``db_query``
for evidence, ``write_file`` to persist a report into the sandbox —
through the real MCP stdio transport, then prints the event stream.

Offline by default: the run uses ``StubLLM`` (unless a key is configured)
and forces ``SEARCH_PROVIDER=none`` — canned results, fully deterministic,
zero network. Pass ``--live`` to honor the configured search provider and
LLM, or set real keys in the environment.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
from pathlib import Path
from typing import Any

from mcp_toolkit.client.agent_loop import AgentLoopDeps, run_agent
from mcp_toolkit.client.events import LlmMessageEvent, format_event
from mcp_toolkit.client.llm import create_llm
from mcp_toolkit.client.mcp_client import McpAgentClient, server_env
from mcp_toolkit.client.server_spawn import resolve_server_spawn
from mcp_toolkit.config import get_settings, load_env_file
from mcp_toolkit.observability.logging import configure_logging
from mcp_toolkit.observability.metrics import create_metrics
from scripts.seed_db import seed_demo_db

QUESTION = (
    "Summarize the reports available in the database, add any relevant "
    "web context, and save a short markdown brief into the sandbox."
)


def _seed_if_missing(db_path: Path) -> bool:
    if db_path.exists():
        return False
    db_path.parent.mkdir(parents=True, exist_ok=True)
    seed_demo_db(db_path)
    return True


def _utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError, OSError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="End-to-end toolkit demo")
    parser.add_argument(
        "--live",
        action="store_true",
        help="honor SEARCH_PROVIDER/LLM_PROVIDER instead of offline defaults",
    )
    return parser.parse_args(argv)


async def _demo(argv: list[str]) -> int:
    args = _parse_args(argv)
    load_env_file()
    # Deterministic by default: canned search results, no network. The
    # spawned server inherits this through the env allow-list.
    if not args.live:
        os.environ["SEARCH_PROVIDER"] = "none"
    settings = get_settings()
    configure_logging(settings.log_level)

    # Seed the fixture DB once; an existing file is left untouched.
    db_path = Path(settings.db_path)
    if await asyncio.to_thread(_seed_if_missing, db_path):
        print(f"seeded fixture database at {db_path}")

    fmt = settings.output_format

    def emit(event: Any) -> None:
        print(format_event(event, fmt))

    spawn = resolve_server_spawn(settings)
    metrics = create_metrics()
    async with McpAgentClient.connect_stdio(spawn.command, spawn.args, env=server_env()) as mcp:
        tools = mcp.list_tools()
        llm = create_llm(settings)
        emit(
            LlmMessageEvent(
                iteration=0,
                content=(
                    f"connected — {len(tools)} tools discovered "
                    f"({', '.join(t.name for t in tools)}), llm={llm.name}"
                ),
            )
        )
        emit(LlmMessageEvent(iteration=0, content=f"question: {QUESTION}"))
        result = await run_agent(
            QUESTION,
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


def main() -> int:
    _utf8_streams()
    try:
        return asyncio.run(_demo(sys.argv[1:]))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
