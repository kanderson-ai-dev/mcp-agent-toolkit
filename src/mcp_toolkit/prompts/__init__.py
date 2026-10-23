"""Prompt Registry — versioned prompt artifacts, a strict status state
machine, a hash-chained audit log, and runtime selection.

Artifacts: ``prompts/<name>/vN.yaml`` (``agent_system``,
``judge_rubric``). Audit: ``data/prompt-registry/audit.jsonl``.
"""

from mcp_toolkit.prompts.audit import AUDIT_EVENT_TYPES, AuditLog, canonical_json
from mcp_toolkit.prompts.errors import (
    InvalidTransition,
    PromptRegistryError,
    RegistryValidationError,
    UnknownPromptVersion,
    UnsafeAuditReason,
)
from mcp_toolkit.prompts.loader import LoadedPrompt, env_var_for, load_prompt, spec_for
from mcp_toolkit.prompts.registry import ALLOWED_TRANSITIONS, PromptRegistry
from mcp_toolkit.prompts.schema import (
    PROMPT_STATUSES,
    VALID_PROMPT_NAMES,
    PromptArtifact,
    PromptStatus,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "AUDIT_EVENT_TYPES",
    "PROMPT_STATUSES",
    "VALID_PROMPT_NAMES",
    "AuditLog",
    "InvalidTransition",
    "LoadedPrompt",
    "PromptArtifact",
    "PromptRegistry",
    "PromptRegistryError",
    "PromptStatus",
    "RegistryValidationError",
    "UnknownPromptVersion",
    "UnsafeAuditReason",
    "canonical_json",
    "env_var_for",
    "load_prompt",
    "spec_for",
]
