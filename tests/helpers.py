"""Shared test helpers (non-fixture utilities)."""

from __future__ import annotations

import json
from typing import Any

from mcp_toolkit.config import Settings


def make_settings(**overrides: Any) -> Settings:
    """Settings without reading the repo ``.env``; kwargs win over env."""
    return Settings(_env_file=None, **overrides)


def parse_envelope(text: str) -> dict[str, Any]:
    """Unwrap the {status, data|code,message} JSON envelope a tool returns."""
    parsed: dict[str, Any] = json.loads(text)
    return parsed
