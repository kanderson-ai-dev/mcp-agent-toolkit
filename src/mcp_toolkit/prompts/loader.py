"""Runtime prompt selection — resolve a spec like
``PROMPT_VERSION_AGENT_SYSTEM=latest-promoted|2`` into a concrete,
fingerprinted :class:`LoadedPrompt`.

The registry is validated eagerly: an invalid registry raises at load
time (fail fast), never a silent fallback to an embedded prompt.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from mcp_toolkit.prompts.registry import PromptRegistry

ENV_PREFIX = "PROMPT_VERSION_"
DEFAULT_REGISTRY_DIR = "prompts"


@dataclass(frozen=True)
class LoadedPrompt:
    """A resolved prompt: template text plus provenance fingerprint."""

    name: str
    version: int
    sha256: str
    template: str

    @property
    def ref(self) -> dict[str, object]:
        """The wire-safe ``{name, version, sha256}`` provenance dict."""
        return {"name": self.name, "version": self.version, "sha256": self.sha256}


def env_var_for(name: str) -> str:
    """``agent_system`` → ``PROMPT_VERSION_AGENT_SYSTEM``."""
    return f"{ENV_PREFIX}{name.upper()}"


def spec_for(name: str, explicit: str | None = None) -> str:
    """Resolution order: explicit spec → env var → ``latest-promoted``."""
    return explicit or os.environ.get(env_var_for(name)) or "latest-promoted"


def load_prompt(
    name: str,
    spec: str | None = None,
    *,
    root_dir: Path | str | None = None,
    registry: PromptRegistry | None = None,
) -> LoadedPrompt:
    """Resolve ``name@spec`` to a fingerprinted prompt.

    ``spec``: ``latest-promoted`` (default) or a version number.
    Unknown versions raise :class:`UnknownPromptVersion`; an invalid
    registry raises :class:`RegistryValidationError`.
    """
    reg = registry or PromptRegistry(
        root_dir or os.environ.get("PROMPT_REGISTRY_DIR", DEFAULT_REGISTRY_DIR)
    )
    artifact = reg.resolve(name, spec_for(name, spec))
    return LoadedPrompt(
        name=artifact.name,
        version=artifact.version,
        sha256=hashlib.sha256(artifact.template.encode("utf-8")).hexdigest(),
        template=artifact.template,
    )
