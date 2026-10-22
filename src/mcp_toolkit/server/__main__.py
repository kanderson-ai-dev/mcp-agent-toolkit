"""MCP server entrypoint — stdio transport.

``mcp-toolkit-server`` or ``python -m mcp_toolkit.server``. All logging
goes to stderr; stdout carries only MCP protocol frames.
"""

from __future__ import annotations

import asyncio

from mcp_toolkit.config import get_settings, load_env_file
from mcp_toolkit.observability.logging import get_logger
from mcp_toolkit.observability.metrics import expose_metrics
from mcp_toolkit.server.app import build_server


def main() -> None:
    load_env_file()
    settings = get_settings()
    built = build_server(settings)
    if settings.metrics_port is not None:
        # /metrics + /healthz on a daemon thread; the canonical transport
        # (stdio) carries no HTTP, so exposition is strictly opt-in.
        expose_metrics(
            built.metrics.registry, settings.metrics_port, get_logger("mcp_toolkit.server")
        )
    asyncio.run(built.server.run_stdio_async())


if __name__ == "__main__":
    main()
