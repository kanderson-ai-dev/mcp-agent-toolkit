"""Real LLM over ``openai`` chat completions.

Only used when ``OPENAI_API_KEY`` is present — otherwise the agent runs
on StubLLM. The adapter is deliberately thin: framework-independent
tool calling, message conversion is explicit and total.
"""

from __future__ import annotations

import json
from typing import Any, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

from mcp_toolkit.client.llm.types import (
    AgentMessage,
    AssistantMessage,
    LLMResponse,
    OpenAIToolDef,
    SystemMessage,
    ToolCallDef,
    ToolMessage,
    UserMessage,
)


class OpenAILLM:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        self._client = client or AsyncOpenAI(api_key=api_key, max_retries=2, timeout=60.0)

    async def complete(
        self, messages: list[AgentMessage], tools: list[OpenAIToolDef]
    ) -> LLMResponse:
        res = await self._client.chat.completions.create(
            model=self._model,
            messages=[_to_openai_message(m) for m in messages],
            tools=cast(list[ChatCompletionToolParam], tools),
            tool_choice="auto",
        )
        msg = res.choices[0].message if res.choices else None
        tool_calls: list[ToolCallDef] = []
        for t in (msg.tool_calls if msg else None) or []:
            if t.type == "function":
                tool_calls.append(
                    ToolCallDef(
                        id=t.id,
                        name=t.function.name,
                        arguments=t.function.arguments,
                    )
                )
        return LLMResponse(content=msg.content if msg else None, tool_calls=tool_calls)


def _to_openai_message(message: AgentMessage) -> ChatCompletionMessageParam:
    match message:
        case SystemMessage():
            return {"role": "system", "content": message.content}
        case UserMessage():
            return {"role": "user", "content": message.content}
        case ToolMessage():
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content,
            }
        case AssistantMessage():
            out: dict[str, Any] = {"role": "assistant", "content": message.content}
            if message.tool_calls:
                out["tool_calls"] = [
                    {
                        "id": t.id,
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "arguments": t.arguments
                            if isinstance(t.arguments, str)
                            else json.dumps(t.arguments),
                        },
                    }
                    for t in message.tool_calls
                ]
            return out  # type: ignore[return-value]
