"""The agent loop: LLM ⇄ MCP tools.

Guaranteed to terminate — bounded by ``max_iterations``. Tool failures
are fed back to the model as tool messages so it can recover or report
honestly. Every step emits a structured :class:`AgentEvent`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import structlog

from mcp_toolkit.client.events import (
    AgentEvent,
    FinalEvent,
    PromptVersionRef,
    ToolCallEvent,
    ToolResultEvent,
    parse_result_envelope,
)
from mcp_toolkit.client.llm.types import (
    AgentMessage,
    AssistantMessage,
    LLMClient,
    OpenAIToolDef,
    SystemMessage,
    ToolCallDef,
    ToolMessage,
    UserMessage,
)
from mcp_toolkit.client.mcp_client import ToolInvoker
from mcp_toolkit.observability.metrics import Metrics
from mcp_toolkit.prompts.loader import LoadedPrompt

_log = structlog.get_logger("mcp_toolkit.client.agent")


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


@dataclass(frozen=True)
class AgentRunResult:
    content: str
    iterations: int
    tool_calls: int
    terminated_by: Literal["answer", "iteration_limit"]


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


async def run_agent(user_prompt: str, deps: AgentLoopDeps) -> AgentRunResult:
    messages: list[AgentMessage] = [
        SystemMessage(content=deps.prompt.template),
        UserMessage(content=user_prompt),
    ]
    tool_call_count = 0

    for iteration in range(1, deps.max_iterations + 1):
        response = await deps.llm.complete(messages, deps.tools)

        if not response.tool_calls:
            content = response.content or ""
            deps.emit(
                FinalEvent(
                    content=content,
                    iterations=iteration,
                    tool_calls=tool_call_count,
                    terminated_by="answer",
                    prompt_version=_prompt_ref(deps),
                )
            )
            _record_run(deps, "answer", iteration)
            return AgentRunResult(
                content=content,
                iterations=iteration,
                tool_calls=tool_call_count,
                terminated_by="answer",
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
    deps.emit(
        FinalEvent(
            content=content,
            iterations=deps.max_iterations,
            tool_calls=tool_call_count,
            terminated_by="iteration_limit",
            prompt_version=_prompt_ref(deps),
        )
    )
    _record_run(deps, "iteration_limit", deps.max_iterations)
    return AgentRunResult(
        content=content,
        iterations=deps.max_iterations,
        tool_calls=tool_call_count,
        terminated_by="iteration_limit",
    )


def _record_run(deps: AgentLoopDeps, terminated_by: str, iterations: int) -> None:
    prompt_version = f"{deps.prompt.name}@v{deps.prompt.version}"
    if deps.metrics is not None:
        deps.metrics.record_agent_run(terminated_by, iterations, prompt_version)
    _log.info(
        "agent_run_finished",
        terminated_by=terminated_by,
        iterations=iterations,
        prompt_version=prompt_version,
        prompt_sha256=deps.prompt.sha256,
    )
