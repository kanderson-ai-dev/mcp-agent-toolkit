"""Rate-limiter tests — ported from the oracle's rate-limiter.test.ts
plus retry-after and check/record split coverage."""

from __future__ import annotations

import pytest

from mcp_toolkit.result import ToolError
from mcp_toolkit.server.guardrails.rate_limiter import RateLimiter


class Clock:
    def __init__(self, t: float = 1_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_allows_up_to_per_tool_limit_then_rejects() -> None:
    limiter = RateLimiter(3)
    for _ in range(3):
        limiter.check("web_search")
        limiter.record("web_search")
    with pytest.raises(ToolError) as exc:
        limiter.check("web_search")
    assert exc.value.code == "rate_limited"
    assert "3/min" in str(exc.value)


def test_tracks_tools_independently() -> None:
    limiter = RateLimiter(1)
    limiter.check("web_search")
    limiter.record("web_search")
    with pytest.raises(ToolError):
        limiter.check("web_search")
    limiter.check("db_query")  # different tool — still allowed


def test_enforces_session_bound_across_tools() -> None:
    # per-tool 5 but session cap 3 → 4th call across any tools fails.
    limiter = RateLimiter(5, session_limit=3)
    for name in ("a", "b", "c"):
        limiter.check(name)
        limiter.record(name)
    with pytest.raises(ToolError, match="Session rate limit"):
        limiter.check("d")


def test_window_slide_frees_capacity() -> None:
    clock = Clock()
    limiter = RateLimiter(2, now=clock)
    limiter.check("x")
    limiter.record("x")
    limiter.check("x")
    limiter.record("x")
    with pytest.raises(ToolError):
        limiter.check("x")
    clock.t += 61.0
    limiter.check("x")  # window elapsed — allowed again


def test_retry_after_seconds_in_message() -> None:
    clock = Clock()
    limiter = RateLimiter(1, now=clock)
    limiter.check("x")
    limiter.record("x")
    with pytest.raises(ToolError, match="retry after 60s"):
        limiter.check("x")
    clock.t += 30.0
    with pytest.raises(ToolError, match="retry after 30s"):
        limiter.check("x")


def test_rejected_check_consumes_no_budget() -> None:
    """check() never records — a rejected attempt cannot eat the window."""
    clock = Clock()
    limiter = RateLimiter(1, now=clock)
    limiter.check("x")
    limiter.record("x")
    for _ in range(5):
        with pytest.raises(ToolError):
            limiter.check("x")
    clock.t += 61.0
    limiter.check("x")  # exactly one hit in the window — passes


def test_check_without_record_does_not_count() -> None:
    limiter = RateLimiter(1)
    for _ in range(10):
        limiter.check("x")  # never recorded → never blocked
