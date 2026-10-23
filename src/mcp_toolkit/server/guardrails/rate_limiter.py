"""Sliding-window rate limiter, in-memory.

Two keys are checked per invocation: ``tool:<name>`` (per-tool bound)
and ``session`` (global bound for the whole client session — one stdio
process = one session, session limit = 4x the per-tool limit by
default). ``now`` is injectable for deterministic tests. Over-limit
calls raise ``rate_limited`` with a retry-after hint — never a hang.

``ToolLimiter`` is the seam: ``RedisRateLimiter`` (opt-in backend, see
``RATE_LIMIT_BACKEND``) shares counters across server processes using
the same check/record contract.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Protocol

from mcp_toolkit.result import ToolError

_WINDOW_SECONDS = 60.0


class ToolLimiter(Protocol):
    """Rate-limit store interface — ``check`` verifies, ``record``
    registers the call (a rejected attempt never consumes budget)."""

    def check(self, tool_name: str) -> None:
        """Raise ``ToolError("rate_limited")`` when a bound is exceeded."""
        ...

    def record(self, tool_name: str) -> None:
        """Register a call after a successful :meth:`check`."""
        ...


class RateLimiter:
    """In-memory sliding-window limiter.

    ``check`` only verifies — :meth:`record` registers the call, so a
    rejected attempt never consumes budget.
    """

    def __init__(
        self,
        per_tool_limit: int,
        now: Callable[[], float] = time.time,
        session_limit: int | None = None,
    ) -> None:
        self._per_tool_limit = per_tool_limit
        self._now = now
        # A session legitimately touches several tools per question.
        self._session_limit = session_limit if session_limit is not None else per_tool_limit * 4
        self._hits: dict[str, list[float]] = {}

    @property
    def per_tool_limit(self) -> int:
        return self._per_tool_limit

    @property
    def session_limit(self) -> int:
        return self._session_limit

    def _kept(self, key: str) -> list[float]:
        cutoff = self._now() - _WINDOW_SECONDS
        kept = [t for t in self._hits.get(key, []) if t > cutoff]
        self._hits[key] = kept
        return kept

    def _retry_after(self, key: str) -> int:
        kept = self._hits.get(key, [])
        if not kept:
            return 0
        oldest = min(kept)
        return max(0, math.ceil(_WINDOW_SECONDS - (self._now() - oldest)))

    def check(self, tool_name: str) -> None:
        """Raise ``ToolError("rate_limited")`` when either bound is exceeded."""
        if len(self._kept(f"tool:{tool_name}")) >= self._per_tool_limit:
            retry = self._retry_after(f"tool:{tool_name}")
            raise ToolError(
                "rate_limited",
                f'Rate limit exceeded for tool "{tool_name}" '
                f"({self._per_tool_limit}/min); retry after {retry}s",
                guardrail="rate_limiter",
            )
        if len(self._kept("session")) >= self._session_limit:
            retry = self._retry_after("session")
            raise ToolError(
                "rate_limited",
                f"Session rate limit exceeded ({self._session_limit} calls/min); "
                f"retry after {retry}s",
                guardrail="rate_limiter",
            )

    def record(self, tool_name: str) -> None:
        """Register a call after a successful :meth:`check`."""
        now = self._now()
        self._hits.setdefault(f"tool:{tool_name}", []).append(now)
        self._hits.setdefault("session", []).append(now)
