"""Request-ID correlation via ``contextvars``.

The agent client stamps a UUID into ``params._meta.requestId`` on every
``tools/call``; the server binds it here so every log line in the
invocation carries ``request_id`` (mirrors the oracle's fallback to the
JSON-RPC request id).
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def bind_request_id(request_id: str) -> Token[str | None]:
    """Bind a request id for the current context; returns a reset token."""
    return _request_id.set(request_id)


def unbind_request_id(token: Token[str | None]) -> None:
    """Restore the previous request id (token from :func:`bind_request_id`)."""
    _request_id.reset(token)


def get_request_id() -> str | None:
    """Current request id, if bound."""
    return _request_id.get()
