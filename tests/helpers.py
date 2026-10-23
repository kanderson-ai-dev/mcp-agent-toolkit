"""Shared test helpers (non-fixture utilities)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from mcp_toolkit.config import Settings
from mcp_toolkit.prompts.loader import LoadedPrompt


def make_settings(**overrides: Any) -> Settings:
    """Settings without reading the repo ``.env``; kwargs win over env."""
    return Settings(_env_file=None, **overrides)


def test_prompt(template: str = "You are a test agent.") -> LoadedPrompt:
    """A deterministic LoadedPrompt for tests — no registry needed."""
    return LoadedPrompt(
        name="agent_system",
        version=1,
        sha256=hashlib.sha256(template.encode()).hexdigest(),
        template=template,
    )


# Imported helpers named ``test_*`` must not be collected as tests.
test_prompt.__test__ = False  # type: ignore[attr-defined]


def parse_envelope(text: str) -> dict[str, Any]:
    """Unwrap the {status, data|code,message} JSON envelope a tool returns."""
    parsed: dict[str, Any] = json.loads(text)
    return parsed
