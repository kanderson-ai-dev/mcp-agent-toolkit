"""Resolve how to spawn the MCP server subprocess.

Explicit override (``MCP_SERVER_COMMAND``/``MCP_SERVER_ARGS``) wins;
the default is ``sys.executable -m mcp_toolkit.server`` — the same
interpreter that's running the client, so venv and path edge cases
disappear. Shared by every entrypoint (CLI agent, web console) so the
resolution logic lives in exactly one place.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from mcp_toolkit.config import Settings


@dataclass(frozen=True)
class ServerSpawn:
    command: str
    args: list[str] = field(default_factory=list)


def resolve_server_spawn(settings: Settings) -> ServerSpawn:
    if settings.mcp_server_command:
        args = (
            [a for a in settings.mcp_server_args.split() if a] if settings.mcp_server_args else []
        )
        return ServerSpawn(command=settings.mcp_server_command, args=args)
    return ServerSpawn(command=sys.executable, args=["-m", "mcp_toolkit.server"])
