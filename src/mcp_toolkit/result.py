"""Uniform tool-result envelope.

Every tool returns JSON text with either ``{"status": "ok", "data": …}``
or ``{"status": "error", "code": …, "message": …}``. Errors surface as
``isError=true`` MCP results (never thrown across the protocol) so the
calling model can read and react to them. Guardrail violations fail
loudly through this envelope — never as a silent empty result.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

ToolErrorCode = Literal[
    "invalid_input",
    "forbidden",
    "not_found",
    "rate_limited",
    "too_large",
    "upstream_error",
    "internal",
]


class ToolError(Exception):
    """A tool-level failure carrying a stable, machine-readable code."""

    def __init__(self, code: ToolErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class OkEnvelope(BaseModel):
    status: Literal["ok"] = "ok"
    data: Any


class ErrEnvelope(BaseModel):
    status: Literal["error"] = "error"
    code: ToolErrorCode | str
    message: str


ToolResultEnvelope = Annotated[OkEnvelope | ErrEnvelope, Field(discriminator="status")]


def ok(data: Any) -> OkEnvelope:
    """Build the ``{"status": "ok", "data": …}`` envelope."""
    return OkEnvelope(data=data)


def err(code: ToolErrorCode | str, message: str) -> ErrEnvelope:
    """Build the ``{"status": "error", …}`` envelope."""
    return ErrEnvelope(code=code, message=message)


def error_result(exc: BaseException) -> ErrEnvelope:
    """Map any exception to an error envelope; unknown → ``internal``."""
    if isinstance(exc, ToolError):
        return err(exc.code, str(exc))
    return err("internal", str(exc) or exc.__class__.__name__)


def envelope_json(envelope: OkEnvelope | ErrEnvelope) -> str:
    """Serialize an envelope as the JSON text carried in MCP content."""
    return envelope.model_dump_json()
