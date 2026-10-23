"""Streamable HTTP transport — the opt-in remote surface.

stdio stays canonical: it needs no sockets, no auth, and inherits the
parent's process lifecycle. Streamable HTTP exists for clients that
cannot spawn a subprocess (remote tools, the MCP Inspector in HTTP
mode). It is **not hardened for the open Internet** — bind loopback
unless you know what you are doing; a bearer token
(``MCP_HTTP_TOKEN``, constant-time compare) is mandatory on any
non-loopback bind.
"""

from __future__ import annotations

import ipaddress
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.mcpserver import MCPServer

_ASGIApp = Callable[[dict[str, Any], Callable[..., Any], Callable[..., Any]], Awaitable[None]]

_LOOPBACK_NAMES = {"localhost", "localhost."}


def is_loopback_host(host: str) -> bool:
    """Literal loopback address or the ``localhost`` name — everything
    else (``0.0.0.0``, LAN IPs, DNS names) counts as remote."""
    if host.lower().strip("[]") in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def require_http_token(host: str, token: str | None) -> None:
    """Fail fast when a remote bind has no bearer token."""
    if not is_loopback_host(host) and not token:
        raise ValueError(
            f"MCP_HTTP_TOKEN is required to bind Streamable HTTP to {host!r} "
            "(non-loopback). Set a token or bind 127.0.0.1."
        )


class BearerOnly:
    """Pure-ASGI middleware: require ``Authorization: Bearer <token>``.

    Compares with :func:`secrets.compare_digest` — no timing oracle.
    Any scope type other than HTTP passes through (there are none on a
    Starlette app, but the middleware stays correct if mounted oddly).
    """

    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}"

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1")
        if not secrets.compare_digest(auth, self._expected):
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"error":"unauthorized: bearer token required"}',
                }
            )
            return
        await self._app(scope, receive, send)


def build_http_app(
    server: MCPServer,
    *,
    host: str,
    token: str | None,
) -> Any:
    """Assemble the Streamable HTTP ASGI app with auth applied.

    ``host`` goes to the SDK for Host-header (DNS-rebinding) checks;
    the bearer middleware wraps it when a token is configured."""
    require_http_token(host, token)
    app: Any = server.streamable_http_app(host=host)
    if token:
        app = BearerOnly(app, token)
    return app
