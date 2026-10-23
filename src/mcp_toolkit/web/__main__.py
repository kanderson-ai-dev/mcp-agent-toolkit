"""Web console entrypoint — ``mcp-toolkit-web``.

The lifespan spawns the MCP server once per web process (not per
request), discovers tools, builds the LLM (same auto/stub selection as
the CLI) and serves the API plus the built console on one port. Closing
the lifespan drops the MCP child — no orphan processes.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import AsyncIterator

import uvicorn
from fastapi import FastAPI

from mcp_toolkit.client.llm import create_llm
from mcp_toolkit.client.mcp_client import McpAgentClient, server_env
from mcp_toolkit.client.server_spawn import resolve_server_spawn
from mcp_toolkit.config import Settings, get_settings, load_env_file
from mcp_toolkit.observability.logging import configure_logging, get_logger
from mcp_toolkit.observability.metrics import create_metrics
from mcp_toolkit.prompts.loader import load_prompt
from mcp_toolkit.web.app import WebDeps, create_app


def _utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError, OSError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def build_app(settings: Settings) -> FastAPI:
    spawn = resolve_server_spawn(settings)
    metrics = create_metrics()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log = get_logger("mcp_toolkit.web")
        async with McpAgentClient.connect_stdio(spawn.command, spawn.args, env=server_env()) as mcp:
            llm = create_llm(settings)
            # Registry resolution happens inside the lifespan — an invalid
            # registry or unknown version aborts startup (fail fast).
            agent_prompt = load_prompt(
                "agent_system",
                settings.prompt_version_agent_system,
                root_dir=settings.prompt_registry_dir,
            )
            app.state.deps = WebDeps(
                invoker=mcp,
                tools=mcp.to_openai_tools(),
                discovered_tools=mcp.list_tools(),
                llm=llm,
                max_iterations=settings.max_tool_iterations,
                agent_prompt=agent_prompt,
                metrics=metrics,
                cors_origin=settings.web_cors_origin or None,
            )
            log.info(
                "web_listen",
                port=settings.web_port,
                llm=llm.name,
                tools=len(mcp.list_tools()),
                prompt_version=f"{agent_prompt.name}@v{agent_prompt.version}",
            )
            yield

    return create_app(lifespan=lifespan)


def main() -> None:
    _utf8_streams()
    load_env_file()
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        build_app(settings),
        host="127.0.0.1",
        port=settings.web_port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
