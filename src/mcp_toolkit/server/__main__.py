"""MCP server entrypoint — stdio transport.

``mcp-toolkit-server`` or ``python -m mcp_toolkit.server``. All logging
goes to stderr; stdout carries only MCP protocol frames.
"""

from __future__ import annotations

import asyncio

from mcp_toolkit.config import get_settings, load_env_file
from mcp_toolkit.server.app import build_server


def main() -> None:
    load_env_file()
    built = build_server(get_settings())
    asyncio.run(built.server.run_stdio_async())


if __name__ == "__main__":
    main()
