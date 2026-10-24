"""Agent-loop tests — sequencing, event emission, and the guaranteed
iteration bound, all with scripted doubles (no LLM, no transport)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from mcp_toolkit.client.agent_loop import AgentLoopDeps, run_agent
from mcp_toolkit.client.events import (
    AgentEvent,
    AnswerDeltaEvent,
    FinalEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from mcp_toolkit.client.llm.types import (
    AgentMessage,
    LLMResponse,
    OpenAIToolDef,
    TokenUsage,
    ToolCallDef,
)
from mcp_toolkit.client.mcp_client import DiscoveredTool, ToolOutcome
from tests.helpers import test_prompt


class ScriptedLLM:
    name = "scripted"
    model = "scripted-model"

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[list[AgentMessage]] = []

    async def complete(
        self,
        messages: list[AgentMessage],
        _tools: list[OpenAIToolDef],
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        return self.responses.pop(0)


class FakeInvoker:
    def __init__(self, outcomes: dict[str, ToolOutcome] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any], str]] = []
        self._outcomes = outcomes or {}

    def list_tools(self) -> list[DiscoveredTool]:
        return []

    async def call(self, name: str, args: dict[str, Any], request_id: str) -> ToolOutcome:
        self.calls.append((name, args, request_id))
        return self._outcomes.get(
            name, ToolOutcome(ok=True, text='{"status": "ok", "data": {}}', duration_ms=1)
        )


def _tool(name: str) -> OpenAIToolDef:
    return {
        "type": "function",
        "function": {"name": name, "description": "", "parameters": {}},
    }


def _deps(
    llm: ScriptedLLM, invoker: FakeInvoker, events: list[AgentEvent], max_iter: int = 8
) -> AgentLoopDeps:
    return AgentLoopDeps(
        llm=llm,
        invoker=invoker,
        tools=[_tool("web_search"), _tool("db_query")],
        max_iterations=max_iter,
        emit=events.append,
        prompt=test_prompt(),
    )


def _call(name: str, args: dict[str, Any] | str = "{}") -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCallDef(
                id=f"id-{name}",
                name=name,
                arguments=args if isinstance(args, str) else json.dumps(args),
            )
        ],
    )


async def test_happy_path_one_call_then_answer() -> None:
    events: list[AgentEvent] = []
    llm = ScriptedLLM([_call("web_search", {"query": "q"}), LLMResponse(content="done")])
    inv = FakeInvoker()
    result = await run_agent("q", _deps(llm, inv, events))

    assert result.terminated_by == "answer"
    assert result.content == "done"
    assert result.tool_calls == 1
    types = [e.type for e in events]
    assert types == ["tool_call", "tool_result", "final"]
    assert inv.calls[0][0] == "web_search"
    assert inv.calls[0][1] == {"query": "q"}


async def test_tool_failure_flows_back_as_tool_message() -> None:
    events: list[AgentEvent] = []
    llm = ScriptedLLM([_call("db_query"), LLMResponse(content="recovered")])
    inv = FakeInvoker(
        {
            "db_query": ToolOutcome(
                ok=False, text='{"status":"error","code":"forbidden","message":"no"}', duration_ms=2
            )
        }
    )
    result = await run_agent("q", _deps(llm, inv, events))

    assert result.terminated_by == "answer"
    tool_msgs = [m for m in llm.calls[1] if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert '"forbidden"' in tool_msgs[0].content
    tr = next(e for e in events if isinstance(e, ToolResultEvent))
    assert tr.ok is False
    assert tr.result == {"status": "error", "code": "forbidden", "message": "no"}


async def test_malformed_arguments_become_empty_dict() -> None:
    events: list[AgentEvent] = []
    llm = ScriptedLLM([_call("web_search", "{not json"), LLMResponse(content="x")])
    inv = FakeInvoker()
    await run_agent("q", _deps(llm, inv, events))
    assert inv.calls[0][1] == {}


async def test_iteration_limit_guarantees_termination() -> None:
    events: list[AgentEvent] = []
    infinite = [
        LLMResponse(
            content=None, tool_calls=[ToolCallDef(id=str(i), name="web_search", arguments="{}")]
        )
        for i in range(10)
    ]
    llm = ScriptedLLM(infinite)
    inv = FakeInvoker()
    result = await run_agent("loop", _deps(llm, inv, events, max_iter=3))

    assert result.terminated_by == "iteration_limit"
    assert result.iterations == 3
    assert result.tool_calls == 3
    final = next(e for e in events if isinstance(e, FinalEvent))
    assert "iteration limit" in final.content


async def test_tool_call_events_carry_request_ids() -> None:
    events: list[AgentEvent] = []
    llm = ScriptedLLM([_call("web_search"), LLMResponse(content="x")])
    inv = FakeInvoker()
    await run_agent("q", _deps(llm, inv, events))
    tc = next(e for e in events if isinstance(e, ToolCallEvent))
    tr = next(e for e in events if isinstance(e, ToolResultEvent))
    assert tc.request_id == tr.request_id == inv.calls[0][2]
    assert len(tc.request_id) == 36


async def test_llm_failure_propagates() -> None:
    events: list[AgentEvent] = []

    class BoomLLM:
        name = "boom"
        model = "boom-model"

        async def complete(
            self,
            _m: list[AgentMessage],
            _t: list[OpenAIToolDef],
            on_delta: Callable[[str], None] | None = None,
        ) -> LLMResponse:
            raise RuntimeError("provider down")

    deps = AgentLoopDeps(
        llm=BoomLLM(),  # type: ignore[arg-type]
        invoker=FakeInvoker(),
        tools=[],
        max_iterations=2,
        emit=events.append,
        prompt=test_prompt(),
    )
    # The oracle propagates LLM failures — the loop must too (caller
    # decides retry/abort); assert the exception surfaces unchanged.
    with pytest.raises(RuntimeError, match="provider down"):
        await run_agent("q", deps)


# ── telemetry + budget gate ───────────────────────────────────────────


def _priced_call(name: str, args: dict[str, Any] | str = "{}") -> LLMResponse:
    call = _call(name, args)
    return LLMResponse(
        content=call.content,
        tool_calls=call.tool_calls,
        usage=TokenUsage(prompt_tokens=1000, completion_tokens=100),
    )


async def test_usage_accumulates_into_final_event() -> None:
    events: list[AgentEvent] = []
    llm = ScriptedLLM(
        [
            _priced_call("web_search"),
            LLMResponse(content="done", usage=TokenUsage(prompt_tokens=800, completion_tokens=50)),
        ]
    )
    result = await run_agent("q", _deps(llm, FakeInvoker(), events))

    assert result.usage.total_tokens == 1950
    final = next(e for e in events if isinstance(e, FinalEvent))
    assert final.usage is not None
    assert final.usage.prompt_tokens == 1800
    assert final.usage.completion_tokens == 150
    assert final.usage.total_tokens == 1950
    assert final.duration_ms >= 0


async def test_cost_usd_computed_when_price_known() -> None:
    events: list[AgentEvent] = []
    llm = ScriptedLLM(
        [LLMResponse(content="x", usage=TokenUsage(prompt_tokens=1_000_000, completion_tokens=0))]
    )
    deps = AgentLoopDeps(
        llm=llm,
        invoker=FakeInvoker(),
        tools=[],
        max_iterations=2,
        emit=events.append,
        prompt=test_prompt(),
        price_per_mtok=(0.50, 1.00),
    )
    result = await run_agent("q", deps)
    assert result.cost_usd == pytest.approx(0.50)
    final = next(e for e in events if isinstance(e, FinalEvent))
    assert final.cost_usd == pytest.approx(0.50)


async def test_cost_none_when_price_unknown() -> None:
    events: list[AgentEvent] = []
    llm = ScriptedLLM(
        [LLMResponse(content="x", usage=TokenUsage(prompt_tokens=5, completion_tokens=5))]
    )
    result = await run_agent("q", _deps(llm, FakeInvoker(), events))
    assert result.cost_usd is None
    final = next(e for e in events if isinstance(e, FinalEvent))
    assert final.cost_usd is None


async def test_budget_gate_stops_before_next_llm_call() -> None:
    """After the first call's usage pushes cost past the cap, the loop
    must stop with ``budget_limit`` *without* calling the LLM again."""
    events: list[AgentEvent] = []
    llm = ScriptedLLM(
        [
            LLMResponse(
                content=None,
                tool_calls=[ToolCallDef(id="t1", name="web_search", arguments="{}")],
                usage=TokenUsage(prompt_tokens=1_000_000, completion_tokens=0),
            ),
            LLMResponse(content="never reached"),
        ]
    )
    deps = AgentLoopDeps(
        llm=llm,
        invoker=FakeInvoker(),
        tools=[_tool("web_search")],
        max_iterations=8,
        emit=events.append,
        prompt=test_prompt(),
        budget_usd=0.40,
        price_per_mtok=(0.50, 1.00),  # $0.50 after call 1 > $0.40 cap
    )
    result = await run_agent("q", deps)

    assert result.terminated_by == "budget_limit"
    assert len(llm.calls) == 1  # the gate fired before call 2
    assert "budget" in result.content.lower()
    final = next(e for e in events if isinstance(e, FinalEvent))
    assert final.terminated_by == "budget_limit"
    assert final.usage is not None and final.usage.total_tokens == 1_000_000


async def test_budget_gate_passes_run_under_cap() -> None:
    events: list[AgentEvent] = []
    llm = ScriptedLLM(
        [LLMResponse(content="x", usage=TokenUsage(prompt_tokens=10, completion_tokens=5))]
    )
    deps = AgentLoopDeps(
        llm=llm,
        invoker=FakeInvoker(),
        tools=[],
        max_iterations=2,
        emit=events.append,
        prompt=test_prompt(),
        budget_usd=5.0,
        price_per_mtok=(0.50, 1.00),
    )
    result = await run_agent("q", deps)
    assert result.terminated_by == "answer"


async def test_budget_gate_unevaluable_without_price() -> None:
    """An unknown-priced model can't feed the gate — the run still
    completes normally (never a fabricated cost)."""
    events: list[AgentEvent] = []
    llm = ScriptedLLM(
        [LLMResponse(content="x", usage=TokenUsage(prompt_tokens=10, completion_tokens=5))]
    )
    deps = AgentLoopDeps(
        llm=llm,
        invoker=FakeInvoker(),
        tools=[],
        max_iterations=2,
        emit=events.append,
        prompt=test_prompt(),
        budget_usd=0.000001,  # would trip instantly if a cost existed
    )
    result = await run_agent("q", deps)
    assert result.terminated_by == "answer"
    assert result.cost_usd is None


async def test_metrics_record_tokens_and_cost() -> None:
    from prometheus_client import generate_latest

    from mcp_toolkit.observability.metrics import create_metrics

    events: list[AgentEvent] = []
    metrics = create_metrics()
    llm = ScriptedLLM(
        [LLMResponse(content="x", usage=TokenUsage(prompt_tokens=100, completion_tokens=40))]
    )
    deps = AgentLoopDeps(
        llm=llm,
        invoker=FakeInvoker(),
        tools=[],
        max_iterations=2,
        emit=events.append,
        prompt=test_prompt(),
        metrics=metrics,
        price_per_mtok=(1.0, 2.0),
    )
    await run_agent("q", deps)
    text = generate_latest(metrics.registry).decode()
    assert 'agent_tokens_total{kind="prompt"} 100.0' in text
    assert 'agent_tokens_total{kind="completion"} 40.0' in text
    assert "agent_cost_usd_total" in text


# ── token streaming + follow-up history ───────────────────────────────


async def test_llm_deltas_become_answer_delta_events() -> None:
    """Every callback chunk is re-emitted as an ``answer_delta`` — the
    ``final`` event still carries the full authoritative content."""

    class DeltaLLM:
        name = "delta"
        model = "delta-model"

        async def complete(
            self,
            _m: list[AgentMessage],
            _t: list[OpenAIToolDef],
            on_delta: Callable[[str], None] | None = None,
        ) -> LLMResponse:
            assert on_delta is not None  # the loop always supplies it
            for chunk in ("Hello ", "world"):
                on_delta(chunk)
            return LLMResponse(content="Hello world")

    events: list[AgentEvent] = []
    deps = AgentLoopDeps(
        llm=DeltaLLM(),  # type: ignore[arg-type]
        invoker=FakeInvoker(),
        tools=[],
        max_iterations=2,
        emit=events.append,
        prompt=test_prompt(),
    )
    result = await run_agent("q", deps)
    deltas = [e.content for e in events if isinstance(e, AnswerDeltaEvent)]
    assert deltas == ["Hello ", "world"]
    final = next(e for e in events if isinstance(e, FinalEvent))
    assert final.content == "Hello world" == result.content


async def test_llm_without_delta_support_still_works() -> None:
    """A complete() that ignores the callback is unaffected."""
    events: list[AgentEvent] = []
    llm = ScriptedLLM([LLMResponse(content="done")])
    result = await run_agent("q", _deps(llm, FakeInvoker(), events))
    assert result.content == "done"
    assert not [e for e in events if isinstance(e, AnswerDeltaEvent)]


async def test_history_is_prepended_between_system_and_question() -> None:
    from mcp_toolkit.client.llm.types import (
        AssistantMessage,
        SystemMessage,
        UserMessage,
    )

    events: list[AgentEvent] = []
    llm = ScriptedLLM([LLMResponse(content="follow-up answer")])
    history: list[AgentMessage] = [
        UserMessage(content="first question"),
        AssistantMessage(content="first answer"),
    ]
    await run_agent("and the second one?", _deps(llm, FakeInvoker(), events), history=history)

    sent = llm.calls[0]
    assert isinstance(sent[0], SystemMessage)
    assert sent[1] == UserMessage(content="first question")
    assert sent[2] == AssistantMessage(content="first answer")
    assert sent[3] == UserMessage(content="and the second one?")
