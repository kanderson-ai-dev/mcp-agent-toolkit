"""MCP server entrypoint — stdio by default, Streamable HTTP opt-in.

``mcp-toolkit-server`` or ``python -m mcp_toolkit.server``. All logging
goes to stderr; stdout carries only MCP protocol frames on stdio.

``MCP_TRANSPORT=streamable-http`` serves the same tools over HTTP —
loopback-only by default; a ``MCP_HTTP_TOKEN`` bearer token is enforced
when configured and *required* on any non-loopback bind. This transport
is not hardened for the open Internet (see ``docs/clients.md``).
"""

from __future__ import annotations

import asyncio

from mcp_toolkit.config import Settings, get_settings, load_env_file
from mcp_toolkit.observability.logging import get_logger
from mcp_toolkit.observability.metrics import expose_metrics
from mcp_toolkit.server.app import BuiltServer, build_server
from mcp_toolkit.server.streamable import build_http_app


def _serve_streamable_http(settings: Settings, built: BuiltServer) -> None:
    import uvicorn

    token = settings.mcp_http_token.get_secret_value() if settings.mcp_http_token else None
    # Validates the loopback-without-token rule before the socket opens.
    app = build_http_app(built.server, host=settings.mcp_http_host, token=token)
    get_logger("mcp_toolkit.server").info(
        "streamable_http_listen",
        host=settings.mcp_http_host,
        port=settings.mcp_http_port,
        auth=bool(token),
    )
    uvicorn.run(
        app,
        host=settings.mcp_http_host,
        port=settings.mcp_http_port,
        # Uvicorn's access logs are plaintext — off the structured stream.
        access_log=False,
        log_level="warning",
    )


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
    if settings.mcp_transport == "streamable-http":
        _serve_streamable_http(settings, built)
    else:
        asyncio.run(built.server.run_stdio_async())


if __name__ == "__main__":
    main()
