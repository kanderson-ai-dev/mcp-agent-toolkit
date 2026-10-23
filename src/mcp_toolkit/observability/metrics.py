"""Prometheus metrics — server invocations, guardrail blocks, agent runs.

Every collector lives on a dedicated :class:`CollectorRegistry` (never the
global default registry) so tests instantiate a fresh ``Metrics`` per case
and no state leaks between runs. Exposition is opt-in: the canonical
transport is stdio, which carries no HTTP, so ``/metrics`` + ``/healthz``
are only served when ``METRICS_PORT`` is configured.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import structlog
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

from mcp_toolkit.client.llm.types import TokenUsage

_DURATION_BUCKETS = (0.005, 0.025, 0.1, 0.5, 1.0, 5.0, 15.0, 30.0)
_ITERATION_BUCKETS = (1, 2, 3, 5, 8, 13, 21)


@dataclass(frozen=True)
class Metrics:
    """All toolkit collectors bound to one registry."""

    registry: CollectorRegistry
    tool_invocations: Counter
    tool_errors: Counter
    tool_duration: Histogram
    guardrail_blocks: Counter
    agent_runs: Counter
    agent_iterations: Histogram
    agent_tokens: Counter
    agent_cost_usd: Counter

    def record_invocation(
        self, tool: str, ok: bool, error_code: str | None, duration_s: float
    ) -> None:
        """One call per tool invocation, labeled by outcome."""
        self.tool_invocations.labels(tool=tool, status="ok" if ok else "error").inc()
        if error_code is not None:
            self.tool_errors.labels(tool=tool, code=error_code).inc()
        self.tool_duration.labels(tool=tool).observe(duration_s)

    def record_guardrail_block(self, guardrail: str) -> None:
        self.guardrail_blocks.labels(guardrail=guardrail).inc()

    def record_agent_run(
        self, terminated_by: str, iterations: int, prompt_version: str = "none"
    ) -> None:
        self.agent_runs.labels(terminated_by=terminated_by, prompt_version=prompt_version).inc()
        self.agent_iterations.observe(iterations)

    def record_run_telemetry(self, usage: TokenUsage, cost_usd: float | None) -> None:
        """Token/cost counters for one finished run — cost only when the
        model's price was known (``None`` is never recorded as 0)."""
        self.agent_tokens.labels(kind="prompt").inc(usage.prompt_tokens)
        self.agent_tokens.labels(kind="completion").inc(usage.completion_tokens)
        if cost_usd is not None:
            self.agent_cost_usd.inc(cost_usd)


def create_metrics(registry: CollectorRegistry | None = None) -> Metrics:
    """Build a ``Metrics`` on a fresh (or supplied) registry."""
    reg = registry if registry is not None else CollectorRegistry()
    return Metrics(
        registry=reg,
        tool_invocations=Counter(
            "mcp_tool_invocations_total",
            "Tool invocations by tool name and outcome",
            ["tool", "status"],
            registry=reg,
        ),
        tool_errors=Counter(
            "mcp_tool_errors_total",
            "Tool invocation failures by tool name and error code",
            ["tool", "code"],
            registry=reg,
        ),
        tool_duration=Histogram(
            "mcp_tool_duration_seconds",
            "Tool invocation latency in seconds",
            ["tool"],
            buckets=_DURATION_BUCKETS,
            registry=reg,
        ),
        guardrail_blocks=Counter(
            "mcp_guardrail_blocks_total",
            "Tool calls rejected by a guardrail, by guardrail name",
            ["guardrail"],
            registry=reg,
        ),
        agent_runs=Counter(
            "agent_runs_total",
            "Agent loop runs by termination reason and prompt version",
            ["terminated_by", "prompt_version"],
            registry=reg,
        ),
        agent_iterations=Histogram(
            "agent_iterations",
            "LLM iterations per agent run",
            buckets=_ITERATION_BUCKETS,
            registry=reg,
        ),
        agent_tokens=Counter(
            "agent_tokens_total",
            "LLM tokens consumed by agent runs, by kind",
            ["kind"],
            registry=reg,
        ),
        agent_cost_usd=Counter(
            "agent_cost_usd_total",
            "Estimated LLM spend across agent runs in USD",
            registry=reg,
        ),
    )


def _handler_for(registry: CollectorRegistry) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — stdlib handler API
            if self.path == "/metrics":
                body = generate_latest(registry)
                self.send_response(200)
                self.send_header("content-type", "text/plain; version=0.0.4")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/healthz":
                body = b"ok"
                self.send_response(200)
                self.send_header("content-type", "text/plain")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass  # access logs would mix with the structured stream

    return Handler


def expose_metrics(
    registry: CollectorRegistry, port: int, logger: structlog.typing.FilteringBoundLogger
) -> ThreadingHTTPServer:
    """Serve ``/metrics`` + ``/healthz`` on a daemon thread; returns the
    server so the caller can shut it down with the process."""
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler_for(registry))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("metrics_listen", port=port)
    return server
