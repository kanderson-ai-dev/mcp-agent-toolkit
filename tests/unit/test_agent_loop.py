"""Agent-loop tests — sequencing, event emission, and the guaranteed
iteration bound, all with scripted doubles (no LLM, no transport)."""

from __future__ import annotations

import json
from typing import Any

from mcp_toolkit.client.agent_loop import AgentLoopDeps, run_agent
from mcp_toolkit.client.events import (
    AgentEvent,
    FinalEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from mcp_toolkit.client.llm.types import (
    AgentMessage,
    LLMResponse,
    OpenAIToolDef,
    ToolCallDef,
)
from mcp_toolkit.client.mcp_client import DiscoveredTool, ToolOutcome
from tests.helpers import test_prompt


class ScriptedLLM:
    name = "scripted"

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[list[AgentMessage]] = []

    async def complete(
        self, messages: list[AgentMessage], _tools: list[OpenAIToolDef]
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

        async def complete(self, _m: list[AgentMessage], _t: list[OpenAIToolDef]) -> LLMResponse:
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
    import pytest

    with pytest.raises(RuntimeError, match="provider down"):
        await run_agent("q", deps)
