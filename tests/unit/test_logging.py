"""Logging tests — JSON on the given stream, redaction, request-id binding."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from mcp_toolkit.observability.logging import (
    _redact,
    configure_logging,
    get_logger,
)
from mcp_toolkit.observability.request_context import (
    bind_request_id,
    get_request_id,
    unbind_request_id,
)


@pytest.fixture()
def log_stream() -> io.StringIO:
    stream = io.StringIO()
    configure_logging("debug", stream=stream)
    return stream


def _records(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


class TestJsonLogging:
    def test_emits_one_json_record_per_event(self, log_stream: io.StringIO) -> None:
        get_logger().info("server_ready", session_id="s1")
        (record,) = _records(log_stream)
        assert record["event"] == "server_ready"
        assert record["level"] == "info"
        assert record["session_id"] == "s1"
        assert "timestamp" in record

    def test_level_filtering(self) -> None:
        stream = io.StringIO()
        configure_logging("error", stream=stream)
        logger = get_logger()
        logger.info("hidden")
        logger.error("shown")
        assert [r["event"] for r in _records(stream)] == ["shown"]


class TestRedaction:
    def test_secret_shaped_keys_are_censored(self, log_stream: io.StringIO) -> None:
        get_logger().info(
            "check",
            api_key="k",
            nested={"authorization": "Bearer abc", "other": 1},
            dsn="postgres://u:p@h/db",
            safe_field="visible",
        )
        (record,) = _records(log_stream)
        assert record["api_key"] == "[redacted]"
        assert record["nested"]["authorization"] == "[redacted]"
        assert record["nested"]["other"] == 1
        assert record["dsn"] == "[redacted]"
        assert record["safe_field"] == "visible"

    def test_secret_shaped_values_are_masked_in_place(self, log_stream: io.StringIO) -> None:
        get_logger().info("leak", note="call me sk-abcdef123456 or Bearer tok-xyz")
        (record,) = _records(log_stream)
        assert "sk-abcdef123456" not in json.dumps(record)
        assert "tok-xyz" not in json.dumps(record)
        assert "[redacted]" in record["note"]

    def test_token_count_keys_stay_visible_telemetry_is_not_secret(
        self, log_stream: io.StringIO
    ) -> None:
        get_logger().info(
            "check",
            total_tokens=1800,
            prompt_tokens=1200,
            completion_tokens=600,
            usage={"tokens": 10},
            nested={
                # noqa: S106 — fake credential for the redaction test
                "auth_token": "real-secret"
            },
        )
        (record,) = _records(log_stream)
        assert record["total_tokens"] == 1800
        assert record["prompt_tokens"] == 1200
        assert record["completion_tokens"] == 600
        assert record["usage"]["tokens"] == 10
        # Credential-shaped keys still censor — counts are the only exemption.
        assert record["nested"]["auth_token"] == "[redacted]"  # noqa: S105

    @given(
        st.dictionaries(
            st.text(min_size=1, max_size=12),
            st.recursive(
                st.one_of(
                    st.text(),
                    st.integers(),
                    st.none(),
                    st.booleans(),
                ),
                lambda children: st.lists(children) | st.dictionaries(st.text(), children),
                max_leaves=8,
            ),
            min_size=1,
            max_size=6,
        )
    )
    @hyp_settings(max_examples=80)
    def test_random_nested_dicts_never_leak_secret_keys(self, event: dict[str, Any]) -> None:
        """Hypothesis: whatever shape lands in the event, values stored
        under secret-looking keys are always replaced by the censor."""
        redacted = _redact(event)

        telemetry_keys = {"tokens", "total_tokens", "prompt_tokens", "completion_tokens"}

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    lowered = str(key).lower()
                    if lowered in telemetry_keys:
                        walk(value)
                    elif any(
                        p in lowered
                        for p in ("key", "token", "password", "secret", "authorization", "dsn")
                    ):
                        assert value == "[redacted]"
                    else:
                        walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(redacted)


class TestRequestContext:
    def test_bind_and_unbind(self) -> None:
        assert get_request_id() is None
        token = bind_request_id("req-1")
        assert get_request_id() == "req-1"
        unbind_request_id(token)
        assert get_request_id() is None

    def test_bound_id_lands_in_log_record(self, log_stream: io.StringIO) -> None:
        token = bind_request_id("req-abc")
        try:
            get_logger().info("tool_invocation", tool="db_query")
        finally:
            unbind_request_id(token)
        (record,) = _records(log_stream)
        assert record["request_id"] == "req-abc"
