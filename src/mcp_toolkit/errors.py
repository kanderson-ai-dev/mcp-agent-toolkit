"""Application-level (non-tool) errors.

Tool failures use :class:`mcp_toolkit.result.ToolError` so they can be
returned as typed envelopes; the exceptions below signal misconfiguration
or lifecycle problems that should abort the caller, not produce a tool
result.
"""

from __future__ import annotations


class McpToolkitError(Exception):
    """Base class for toolkit errors."""


class ConfigError(McpToolkitError):
    """A required setting is missing or inconsistent (e.g. a provider
    selected without its credentials)."""
