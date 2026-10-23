"""The agent loop: LLM ⇄ MCP tools.

Guaranteed to terminate — bounded by ``max_iterations`` and the optional
``budget_usd`` gate (enforced in code before each LLM call, never by the
prompt). Tool failures are fed back to the model as tool messages so it
can recover or report honestly. Every step emits a structured
:class:`AgentEvent`.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal

import structlog

from mcp_toolkit.client.events import (
    AgentEvent,
    AnswerDeltaEvent,
    FinalEvent,
    PromptVersionRef,
    ToolCallEvent,
    ToolResultEvent,
    UsageSummary,
    parse_result_envelope,
)
from mcp_toolkit.client.llm.types import (
    AgentMessage,
    AssistantMessage,
    LLMClient,
    OpenAIToolDef,
    SystemMessage,
    TokenUsage,
    ToolCallDef,
    ToolMessage,
    UserMessage,
)
from mcp_toolkit.client.mcp_client import ToolInvoker
from mcp_toolkit.observability.metrics import Metrics
from mcp_toolkit.prompts.loader import LoadedPrompt

_log = structlog.get_logger("mcp_toolkit.client.agent")

Termination = Literal["answer", "iteration_limit", "budget_limit"]


@dataclass(frozen=True)
class AgentLoopDeps:
    llm: LLMClient
    invoker: ToolInvoker
    tools: list[OpenAIToolDef]
    max_iterations: int
    emit: Callable[[AgentEvent], None]
    prompt: LoadedPrompt
    """System prompt resolved from the registry — template + fingerprint."""
    metrics: Metrics | None = None
    budget_usd: float | None = None
    """Hard cap on accumulated LLM cost — checked before each LLM call."""
    price_per_mtok: tuple[float, float] | None = None
    """(prompt, completion) USD per 1M tokens, resolved by the caller via
    ``observability.pricing`` — ``None`` when the model's price is
    unknown, in which case cost stays ``None`` and the budget gate cannot
    evaluate (logged once, never guessed)."""


@dataclass(frozen=True)
class AgentRunResult:
    content: str
    iterations: int
    tool_calls: int
    terminated_by: Termination
    usage: TokenUsage
    cost_usd: float | None
    duration_ms: int


def _emit_delta(deps: AgentLoopDeps, iteration: int, text: str) -> None:
    """One streamed token chunk → one ``answer_delta`` event (bound via
    ``partial`` so each iteration's number travels with the chunk)."""
    deps.emit(AnswerDeltaEvent(iteration=iteration, content=text))


def _parse_tool_args(call: ToolCallDef) -> dict[str, Any]:
    """Malformed JSON arguments are reported back to the model, not thrown."""
    try:
        parsed = json.loads(call.arguments)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _prompt_ref(deps: AgentLoopDeps) -> PromptVersionRef:
    return PromptVersionRef(
        name=deps.prompt.name,
        version=deps.prompt.version,
        sha256=deps.prompt.sha256,
    )


def _cost_so_far(deps: AgentLoopDeps, usage: TokenUsage) -> float | None:
    if deps.price_per_mtok is None:
        return None
    prompt_price, completion_price = deps.price_per_mtok
    return (usage.prompt_tokens * prompt_price + usage.completion_tokens * completion_price) / 1e6


def _final(
    deps: AgentLoopDeps,
    content: str,
    iterations: int,
    tool_calls: int,
    terminated_by: Termination,
    usage: TokenUsage,
    started: float,
) -> AgentRunResult:
    """Emit the ``final`` event, record metrics/logs, return the result —
    one exit point so telemetry can never drift between terminations."""
    duration_ms = round((time.perf_counter() - started) * 1000)
    cost = _cost_so_far(deps, usage)
    deps.emit(
        FinalEvent(
            content=content,
            iterations=iterations,
            tool_calls=tool_calls,
            terminated_by=terminated_by,
            duration_ms=duration_ms,
            usage=UsageSummary(
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
            ),
            cost_usd=cost,
            prompt_version=_prompt_ref(deps),
        )
    )
    _record_run(deps, terminated_by, iterations, usage, cost, duration_ms)
    return AgentRunResult(
        content=content,
        iterations=iterations,
        tool_calls=tool_calls,
        terminated_by=terminated_by,
        usage=usage,
        cost_usd=cost,
        duration_ms=duration_ms,
    )


async def run_agent(
    user_prompt: str,
    deps: AgentLoopDeps,
    history: list[AgentMessage] | None = None,
) -> AgentRunResult:
    """Run the loop. ``history`` carries prior user/assistant turns for
    follow-up questions — already validated/bounded by the caller."""
    messages: list[AgentMessage] = [
        SystemMessage(content=deps.prompt.template),
        *(history or []),
        UserMessage(content=user_prompt),
    ]
    tool_call_count = 0
    started = time.perf_counter()
    usage = TokenUsage(prompt_tokens=0, completion_tokens=0)
    budget_warned = False

    for iteration in range(1, deps.max_iterations + 1):
        if deps.budget_usd is not None:
            cost = _cost_so_far(deps, usage)
            if cost is None:
                if not budget_warned:
                    _log.warning(
                        "agent_budget_unevaluable",
                        model="unknown price — budget gate cannot evaluate",
                    )
                    budget_warned = True
            elif cost >= deps.budget_usd:
                content = (
                    f"Stopped: run budget reached (${cost:.4f} of ${deps.budget_usd:.4f} spent)."
                )
                return _final(
                    deps,
                    content,
                    iteration - 1,
                    tool_call_count,
                    "budget_limit",
                    usage,
                    started,
                )

        response = await deps.llm.complete(
            messages,
            deps.tools,
            on_delta=partial(_emit_delta, deps, iteration),
        )
        if response.usage is not None:
            usage = TokenUsage(
                prompt_tokens=usage.prompt_tokens + response.usage.prompt_tokens,
                completion_tokens=usage.completion_tokens + response.usage.completion_tokens,
            )

        if not response.tool_calls:
            return _final(
                deps,
                response.content or "",
                iteration,
                tool_call_count,
                "answer",
                usage,
                started,
            )

        messages.append(
            AssistantMessage(content=response.content, tool_calls=tuple(response.tool_calls))
        )

        for call in response.tool_calls:
            tool_call_count += 1
            request_id = str(uuid.uuid4())
            args = _parse_tool_args(call)
            deps.emit(
                ToolCallEvent(
                    iteration=iteration,
                    request_id=request_id,
                    tool=call.name,
                    args=args,
                )
            )
            # The same request_id reaches the event stream, this client
            # log line, and (via params._meta.requestId) the server log.
            _log.info("agent_tool_call", tool=call.name, request_id=request_id, iteration=iteration)
            outcome = await deps.invoker.call(call.name, args, request_id)
            _log.info(
                "agent_tool_result",
                tool=call.name,
                request_id=request_id,
                ok=outcome.ok,
                duration_ms=outcome.duration_ms,
            )
            deps.emit(
                ToolResultEvent(
                    iteration=iteration,
                    request_id=request_id,
                    tool=call.name,
                    ok=outcome.ok,
                    duration_ms=outcome.duration_ms,
                    preview=outcome.text[:500],
                    result=parse_result_envelope(outcome.text),
                )
            )
            messages.append(ToolMessage(tool_call_id=call.id, name=call.name, content=outcome.text))

    content = (
        f"Stopped: reached the iteration limit ({deps.max_iterations}) without a final answer."
    )
    return _final(
        deps,
        content,
        deps.max_iterations,
        tool_call_count,
        "iteration_limit",
        usage,
        started,
    )


def _record_run(
    deps: AgentLoopDeps,
    terminated_by: str,
    iterations: int,
    usage: TokenUsage,
    cost_usd: float | None,
    duration_ms: int,
) -> None:
    prompt_version = f"{deps.prompt.name}@v{deps.prompt.version}"
    if deps.metrics is not None:
        deps.metrics.record_agent_run(terminated_by, iterations, prompt_version)
        deps.metrics.record_run_telemetry(usage, cost_usd)
    _log.info(
        "agent_run_finished",
        terminated_by=terminated_by,
        iterations=iterations,
        model=deps.llm.model,
        prompt_version=prompt_version,
        prompt_sha256=deps.prompt.sha256,
        total_tokens=usage.total_tokens,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
    )
