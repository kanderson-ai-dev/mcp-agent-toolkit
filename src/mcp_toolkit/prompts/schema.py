"""Prompt artifact schema — the versioned unit of the registry.

Artifacts live at ``prompts/<name>/vN.yaml`` and are pure data: a
validated, size-capped template plus provenance metadata. Extra keys
are forbidden so a misspelled field fails loudly instead of being
silently dropped.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

PromptStatus = Literal["draft", "candidate", "promoted", "rolled_back"]
PROMPT_STATUSES: tuple[str, ...] = ("draft", "candidate", "promoted", "rolled_back")

VALID_PROMPT_NAMES: tuple[str, ...] = ("agent_system", "judge_rubric")

MAX_TEMPLATE_CHARS = 32_000
MAX_FIELD_CHARS = 2_000

ARTIFACT_FILENAME_RE = r"v(\d+)\.yaml"


class _LiteralStr(str):
    """Marker: dump this string as a YAML block literal (``|-``)."""


def _literal_str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


yaml.SafeDumper.add_representer(_LiteralStr, _literal_str_representer)


class PromptArtifact(BaseModel):
    """One versioned prompt artifact. ``template`` is data, never code."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    version: int = Field(ge=1)
    status: PromptStatus
    author: str = Field(min_length=1, max_length=128)
    created_at: datetime
    description: str = Field(min_length=1, max_length=MAX_FIELD_CHARS)
    model_hint: str | None = Field(default=None, max_length=128)
    template: str = Field(min_length=1, max_length=MAX_TEMPLATE_CHARS)

    @property
    def sha256(self) -> str:
        import hashlib

        return hashlib.sha256(self.template.encode("utf-8")).hexdigest()


def load_artifact(path: Path) -> PromptArtifact:
    """Load and validate a single ``vN.yaml`` artifact file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: artifact file is not a YAML mapping")
    return PromptArtifact.model_validate(raw)


def dump_artifact(artifact: PromptArtifact) -> str:
    """Serialize an artifact to YAML — stable key order, block-literal
    template so prompts stay human-readable and diffs stay clean."""
    data = artifact.model_dump(mode="python")
    data["created_at"] = artifact.created_at.isoformat()
    if artifact.model_hint is None:
        data.pop("model_hint")
    data["template"] = _LiteralStr(artifact.template)
    dumped: str = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120)
    return dumped
