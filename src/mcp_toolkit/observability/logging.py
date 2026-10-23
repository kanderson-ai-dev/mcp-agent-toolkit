"""Structured JSON logging on **stderr** — stdout is reserved for the
MCP stdio transport and must stay clean.

Redaction is unconditional (defense-in-depth: never log config or env
objects): any key matching ``*key*``, ``*token*``, ``*password*``,
``*secret*``, ``authorization`` or ``*dsn*`` is censored recursively,
and values that look like ``sk-…``/``Bearer …``/provider tokens are
masked even under innocent-looking field names.
"""

from __future__ import annotations

import re
import sys
from collections.abc import MutableMapping
from typing import Any, TextIO, cast

import structlog
from structlog.typing import EventDict

from mcp_toolkit.observability.request_context import get_request_id

_CENSOR = "[redacted]"
_SECRET_KEY_PARTS = ("key", "token", "password", "secret", "authorization", "dsn")
_SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{6,}|pk-[A-Za-z0-9_\-]{6,}|lsv2_[A-Za-z0-9_]+|Bearer\s+\S+)"
)

# Exact-match allowlist: token *counts* are telemetry, never credentials.
# Consulted before the substring match so usage logging stays readable.
_NON_SECRET_TELEMETRY_KEYS = frozenset(
    {"tokens", "total_tokens", "prompt_tokens", "completion_tokens"}
)

_LOG_LEVELS: dict[str, int] = {
    "debug": 10,
    "info": 20,
    "warning": 30,
    "warn": 30,
    "error": 40,
    "critical": 50,
}


def _level_number(level: str) -> int:
    return _LOG_LEVELS.get(level.strip().lower(), 20)


def _redact(value: Any) -> Any:
    """Recursively censor secret keys and secret-shaped values."""
    if isinstance(value, MutableMapping):
        return {k: (_CENSOR if _is_secret_key(k) else _redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact(v) for v in value]
    if isinstance(value, str) and _SECRET_VALUE_RE.search(value):
        return _SECRET_VALUE_RE.sub(_CENSOR, value)
    return value


def _is_secret_key(key: Any) -> bool:
    lowered = str(key).lower()
    if lowered in _NON_SECRET_TELEMETRY_KEYS:
        return False
    return any(part in lowered for part in _SECRET_KEY_PARTS)


def redact_secrets(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    """structlog processor: recursive secret redaction on the event dict."""
    return cast(EventDict, _redact(dict(event_dict)))


def merge_request_context(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    """structlog processor: attach the bound request id, if any."""
    request_id = get_request_id()
    if request_id is not None:
        event_dict.setdefault("request_id", request_id)
    return event_dict


def configure_logging(level: str = "info", *, stream: TextIO | None = None) -> None:
    """Configure process-wide structlog JSON logging to ``stream`` (stderr).

    ``stream`` is injectable so tests can capture log output without
    touching the real stderr.
    """
    out = stream if stream is not None else sys.stderr
    structlog.configure(
        processors=[
            merge_request_context,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_secrets,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_level_number(level)),
        logger_factory=structlog.PrintLoggerFactory(file=out),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> Any:
    """Return a bound structlog logger."""
    return structlog.get_logger(name) if name else structlog.get_logger()
