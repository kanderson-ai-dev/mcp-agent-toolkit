"""Typed errors for the prompt registry — callers distinguish a bad
registry on disk from a missing version from an illegal transition."""

from __future__ import annotations


class PromptRegistryError(RuntimeError):
    """Base class for all registry failures."""


class RegistryValidationError(PromptRegistryError):
    """The on-disk registry is structurally invalid (bad YAML, schema
    violations, zero or multiple promoted versions of a name). Raised at
    load time so the app fails fast instead of silently falling back."""


class UnknownPromptVersion(PromptRegistryError):
    """A requested ``name@version`` does not exist in the registry."""


class InvalidTransition(PromptRegistryError):
    """A status transition that ``ALLOWED_TRANSITIONS`` forbids."""


class UnsafeAuditReason(PromptRegistryError):
    """An audit reason containing a key- or secret-looking string —
    refused rather than persisted in the append-only log."""
