"""SSE wire helpers — ``AgentEvent`` → named ``text/event-stream`` frames.

``event:`` carries the discriminant (``tool_call``, ``tool_result``, …)
and ``data:`` the full camelCase JSON payload, so the frontend can
subscribe per event type via ``EventSource.addEventListener``.
"""

from __future__ import annotations

from mcp_toolkit.client.events import AgentEvent

# SSE comment line — keeps proxies and browsers from idle-closing the
# stream during long LLM calls. Invisible to EventSource listeners.
HEARTBEAT = b": heartbeat\n\n"


def frame(event: AgentEvent) -> bytes:
    """One named SSE frame for ``event``."""
    data = event.model_dump_json(by_alias=True)
    return f"event: {event.type}\ndata: {data}\n\n".encode()
