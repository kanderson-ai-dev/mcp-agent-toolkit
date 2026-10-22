"""Tool-definition types shared by the registry and the guarded wrapper."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolContext:
    """Per-invocation context propagated through the guardrail chain."""

    request_id: str
    """Client-supplied via ``params._meta.requestId``, else the JSON-RPC ID."""
    session_id: str
    """Server process instance ID — correlates a whole client↔server session."""


ToolHandler = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolDef:
    """A named tool: description shown to the model + the data-returning
    handler. Envelope wrapping, rate limiting and sanitization live in the
    guarded wrapper, not here."""

    name: str
    description: str
    handler: ToolHandler
