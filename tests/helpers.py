"""Shared test helpers (non-fixture utilities)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
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


def copy_promoted_baseline(src: Path, dst: Path) -> Path:
    """Copy the real ``prompts/`` tree pinned to v1 artifacts only.

    Real later versions (v2.yaml, …) are filtered out so tests create
    their own predictable next version, and each copied v1 is restored to
    ``status: promoted`` — the on-disk tree may have rolled it back after
    a real promotion, which would leave a v1-only copy with no promoted
    prompt and fail registry loading."""
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("v[2-9]*.yaml", "v1[0-9]*.yaml"),
    )
    for artifact in dst.rglob("v1.yaml"):
        artifact.write_text(
            re.sub(
                r"^status: .*$",
                "status: promoted",
                artifact.read_text(encoding="utf-8"),
                count=1,
                flags=re.M,
            ),
            encoding="utf-8",
        )
    return dst
