"""Provider-agnostic LLM types for the agent loop.

The loop speaks these types only — OpenAI is one implementation, StubLLM
another — so tests and offline runs never depend on a live provider.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypedDict


@dataclass(frozen=True)
class ToolCallDef:
    id: str
    name: str
    arguments: str
    """Raw JSON string, exactly as the model produced it."""


class FunctionDef(TypedDict, total=False):
    name: str
    description: str
    parameters: dict[str, Any]


class OpenAIToolDef(TypedDict):
    type: Literal["function"]
    function: FunctionDef


@dataclass(frozen=True)
class SystemMessage:
    content: str
    role: Literal["system"] = "system"


@dataclass(frozen=True)
class UserMessage:
    content: str
    role: Literal["user"] = "user"


@dataclass(frozen=True)
class AssistantMessage:
    content: str | None
    tool_calls: tuple[ToolCallDef, ...] = ()
    role: Literal["assistant"] = "assistant"


@dataclass(frozen=True)
class ToolMessage:
    tool_call_id: str
    name: str
    content: str
    role: Literal["tool"] = "tool"


AgentMessage = SystemMessage | UserMessage | AssistantMessage | ToolMessage


@dataclass(frozen=True)
class TokenUsage:
    """Token accounting reported by the provider for one completion."""

    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class LLMResponse:
    content: str | None
    tool_calls: Sequence[ToolCallDef] = field(default_factory=tuple)
    usage: TokenUsage | None = None
    """Additive/optional: token accounting when the provider reports it."""


class LLMClient(Protocol):
    name: str
    model: str
    """The model id used for price lookup — real model name for live
    providers, ``"stub"`` for the deterministic stub (unpriced)."""

    async def complete(
        self, messages: list[AgentMessage], tools: list[OpenAIToolDef]
    ) -> LLMResponse: ...
