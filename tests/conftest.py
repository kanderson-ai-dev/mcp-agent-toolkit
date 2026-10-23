"""Shared pytest fixtures.

Guarantee the suite stays offline and secret-free: every test runs with a
clean credential environment unless ``RUN_LIVE_TESTS=1`` is set, and
``live``-marked tests skip by default.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

ALL_SETTINGS_ENV_VARS = (
    "OPENAI_API_KEY",
    "CHAT_MODEL_NAME",
    "LLM_PROVIDER",
    "SEARCH_PROVIDER",
    "SEARCH_API_KEY",
    "MCP_SERVER_COMMAND",
    "MCP_SERVER_ARGS",
    "SANDBOX_ROOT",
    "DB_PATH",
    "RATE_LIMIT_PER_MINUTE",
    "RATE_LIMIT_BACKEND",
    "RATE_LIMIT_KEY_PREFIX",
    "REDIS_URL",
    "MAX_TOOL_ITERATIONS",
    "MAX_TOOL_OUTPUT_CHARS",
    "READ_FILE_MAX_BYTES",
    "WRITE_FILE_MAX_BYTES",
    "LOG_LEVEL",
    "METRICS_PORT",
    "OUTPUT_FORMAT",
    "LANGCHAIN_TRACING_V2",
    "LANGCHAIN_API_KEY",
    "LANGCHAIN_PROJECT",
    "WEB_PORT",
    "WEB_CORS_ORIGIN",
)


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Unset every ``Settings`` env var so tests are fully deterministic."""
    for name in ALL_SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


SECRET_ENV_VARS = (
    "OPENAI_API_KEY",
    "SEARCH_API_KEY",
    "LANGCHAIN_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "MCP_HTTP_TOKEN",
    "REDIS_URL",
)

LIVE_ENV_VARS = ("RUN_LIVE_TESTS",)


def _live_enabled() -> bool:
    return os.environ.get("RUN_LIVE_TESTS") == "1"


@pytest.fixture(autouse=True, scope="session")
def _offline_env() -> Iterator[None]:
    """Strip real credentials for the whole session unless live tests are on."""
    if _live_enabled():
        yield
        return
    saved = {k: os.environ.pop(k, None) for k in SECRET_ENV_VARS}
    yield
    for key, value in saved.items():
        if value is not None:
            os.environ[key] = value


def _e2e_enabled(config: pytest.Config) -> bool:
    """E2E runs only when targeted explicitly (``pytest tests/e2e``) or
    opted into — the sync Playwright driver keeps its greenlet loop
    alive between tests, which breaks ``pytest-asyncio``'s runner for
    every async test collected in the same session."""
    if os.environ.get("RUN_E2E_TESTS") == "1":
        return True
    return any("e2e" in str(arg) for arg in config.args)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not _e2e_enabled(config):
        skip_e2e = pytest.mark.skip(reason="needs RUN_E2E_TESTS=1 or pytest tests/e2e")
        for item in items:
            if "e2e" in item.keywords:
                item.add_marker(skip_e2e)
    if _live_enabled():
        return
    skip_live = pytest.mark.skip(reason="needs RUN_LIVE_TESTS=1")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
