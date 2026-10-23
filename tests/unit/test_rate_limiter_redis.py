"""RedisRateLimiter — same sliding-window contract as the in-memory
limiter, verified against fakeredis (offline, deterministic clock)."""

from __future__ import annotations

import fakeredis
import pytest

from mcp_toolkit.result import ToolError
from mcp_toolkit.server.app import _build_limiter
from mcp_toolkit.server.guardrails.rate_limiter import RateLimiter
from mcp_toolkit.server.guardrails.rate_limiter_redis import RedisRateLimiter
from tests.helpers import make_settings


def test_check_then_record_allows_then_blocks() -> None:
    r = fakeredis.FakeRedis()
    limiter = RedisRateLimiter(r, per_tool_limit=2)
    for _ in range(2):
        limiter.check("db_query")
        limiter.record("db_query")
    with pytest.raises(ToolError) as exc:
        limiter.check("db_query")
    assert exc.value.code == "rate_limited"
    assert exc.value.guardrail == "rate_limiter"


def test_window_slides_with_injected_clock() -> None:
    now = [1_000.0]
    r = fakeredis.FakeRedis()
    limiter = RedisRateLimiter(r, per_tool_limit=1, now=lambda: now[0])
    limiter.check("read_file")
    limiter.record("read_file")
    with pytest.raises(ToolError):
        limiter.check("read_file")
    now[0] += 61.0  # past the 60s window
    limiter.check("read_file")  # allowed again
    limiter.record("read_file")


def test_session_bound_is_independent_of_tool_bound() -> None:
    now = [1_000.0]
    r = fakeredis.FakeRedis()
    limiter = RedisRateLimiter(r, per_tool_limit=10, session_limit=3, now=lambda: now[0])
    for i in range(3):
        limiter.check("read_file" if i == 0 else f"tool_{i}")
        limiter.record("read_file" if i == 0 else f"tool_{i}")
    with pytest.raises(ToolError, match="Session rate limit"):
        limiter.check("db_query")


def test_error_message_reports_retry_after_and_limit() -> None:
    now = [1_000.0]
    r = fakeredis.FakeRedis()
    limiter = RedisRateLimiter(r, per_tool_limit=1, now=lambda: now[0])
    limiter.record("web_search")
    with pytest.raises(ToolError, match=r"1/min\); retry after"):
        limiter.check("web_search")


def test_two_limiter_instances_share_state_via_redis() -> None:
    """The whole point of the backend: a second process sees the first
    process's calls — the session bound is enforced cluster-wide."""
    r = fakeredis.FakeRedis()
    a = RedisRateLimiter(r, per_tool_limit=1)
    b = RedisRateLimiter(r, per_tool_limit=1)
    a.check("db_query")
    a.record("db_query")
    with pytest.raises(ToolError, match="Rate limit"):
        b.check("db_query")


class _Log:
    def info(self, *_a: object, **_kw: object) -> None: ...


def test_build_limiter_defaults_to_memory() -> None:
    limiter = _build_limiter(make_settings(), _Log())
    assert isinstance(limiter, RateLimiter)


def test_build_limiter_redis_requires_url() -> None:
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        _build_limiter(make_settings(rate_limit_backend="redis"), _Log())


def test_build_limiter_redis_connects_when_configured() -> None:
    """redis is installed in dev — a fakeredis URL shape still exercises
    the wiring; connection failures would raise at ``from_url``'s ping."""
    from unittest.mock import patch

    fake = fakeredis.FakeRedis()
    with patch("redis.Redis.from_url", return_value=fake):
        limiter = _build_limiter(
            make_settings(
                rate_limit_backend="redis",
                redis_url="redis://localhost:6379/0",  # noqa: S106
            ),
            _Log(),
        )
    assert isinstance(limiter, RedisRateLimiter)
