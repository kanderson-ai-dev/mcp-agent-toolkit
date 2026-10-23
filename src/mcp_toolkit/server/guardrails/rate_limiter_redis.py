"""Redis-backed rate limiter — opt-in backend behind ``ToolLimiter``.

Same sliding-window semantics as :class:`RateLimiter`, with counters in
shared Redis sorted sets so several MCP server processes enforce one
budget. Each key is a sorted set of ``timestamp:uuid`` members scored by
time; the window is trimmed on every access.

**Deliberately not the default** (``RATE_LIMIT_BACKEND=redis`` +
``REDIS_URL``): the sync ``redis`` client blocks the event loop for a
sub-millisecond localhost round-trip per invocation — acceptable for a
single-user console, and keeps the interface synchronous so the
in-memory backend stays allocation-free. ``redis`` is an optional extra
(``pip install .[redis]``); selecting the backend without the package
fails loudly at startup. Like the in-memory backend, check→record is
two operations — a small race under heavy concurrency is acceptable for
a guardrail (worst case: one extra admitted call per race window).
"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Callable

from mcp_toolkit.result import ToolError

from .rate_limiter import _WINDOW_SECONDS

_DEFAULT_PREFIX = "mcp-toolkit:rate-limit"


class RedisRateLimiter:
    """Shared sliding-window limiter over Redis sorted sets."""

    def __init__(
        self,
        client: object,
        per_tool_limit: int,
        session_limit: int | None = None,
        now: Callable[[], float] = time.time,
        key_prefix: str = _DEFAULT_PREFIX,
    ) -> None:
        # ``client`` is typed loosely — anything quacking like
        # ``redis.Redis`` (zadd/zcard/…) works; fakeredis for tests.
        self._redis = client
        self._per_tool_limit = per_tool_limit
        self._session_limit = session_limit if session_limit is not None else per_tool_limit * 4
        self._now = now
        self._prefix = key_prefix

    @classmethod
    def from_url(
        cls,
        url: str,
        per_tool_limit: int,
        session_limit: int | None = None,
        key_prefix: str = _DEFAULT_PREFIX,
    ) -> RedisRateLimiter:
        """Build from ``REDIS_URL``; the optional dependency failing to
        import is a loud startup error, never a silent fallback."""
        import redis

        client = redis.Redis.from_url(url, socket_timeout=2.0, socket_connect_timeout=2.0)
        client.ping()  # fail at boot, not on the first tool call
        return cls(client, per_tool_limit, session_limit, key_prefix=key_prefix)

    @property
    def per_tool_limit(self) -> int:
        return self._per_tool_limit

    @property
    def session_limit(self) -> int:
        return self._session_limit

    def _key(self, scope: str) -> str:
        return f"{self._prefix}:{scope}"

    def _count(self, key: str) -> int:
        """Members inside the sliding window (stale entries trimmed)."""
        self._redis.zremrangebyscore(key, "-inf", self._now() - _WINDOW_SECONDS)  # type: ignore[attr-defined]
        return int(self._redis.zcard(key))  # type: ignore[attr-defined]

    def _oldest(self, key: str) -> float | None:
        first = self._redis.zrange(key, 0, 0, withscores=True)  # type: ignore[attr-defined]
        return float(first[0][1]) if first else None

    def check(self, tool_name: str) -> None:
        """Raise ``ToolError("rate_limited")`` when either bound is exceeded."""
        tool_key = self._key(f"tool:{tool_name}")
        if self._count(tool_key) >= self._per_tool_limit:
            oldest = self._oldest(tool_key)
            retry = (
                max(0, math.ceil(_WINDOW_SECONDS - (self._now() - oldest)))
                if oldest is not None
                else 0
            )
            raise ToolError(
                "rate_limited",
                f'Rate limit exceeded for tool "{tool_name}" '
                f"({self._per_tool_limit}/min); retry after {retry}s",
                guardrail="rate_limiter",
            )
        session_key = self._key("session")
        if self._count(session_key) >= self._session_limit:
            oldest = self._oldest(session_key)
            retry = (
                max(0, math.ceil(_WINDOW_SECONDS - (self._now() - oldest)))
                if oldest is not None
                else 0
            )
            raise ToolError(
                "rate_limited",
                f"Session rate limit exceeded ({self._session_limit} calls/min); "
                f"retry after {retry}s",
                guardrail="rate_limiter",
            )

    def record(self, tool_name: str) -> None:
        """Register a call after a successful :meth:`check`."""
        now = self._now()
        cutoff = now - _WINDOW_SECONDS
        expire_ms = int(_WINDOW_SECONDS * 1000) * 2
        for scope in (f"tool:{tool_name}", "session"):
            key = self._key(scope)
            self._redis.zadd(key, {f"{now}:{uuid.uuid4().hex}": now})  # type: ignore[attr-defined]
            self._redis.zremrangebyscore(key, "-inf", cutoff)  # type: ignore[attr-defined]
            self._redis.pexpire(key, expire_ms)  # type: ignore[attr-defined]
