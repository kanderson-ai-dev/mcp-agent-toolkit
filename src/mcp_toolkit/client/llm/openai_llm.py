"""Real LLM over ``openai`` chat completions.

Only used when ``OPENAI_API_KEY`` is present — otherwise the agent runs
on StubLLM. The adapter is deliberately thin: framework-independent
tool calling, message conversion is explicit and total.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

from mcp_toolkit.client.llm.types import (
    AgentMessage,
    AssistantMessage,
    LLMResponse,
    OpenAIToolDef,
    SystemMessage,
    TokenUsage,
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
        self.model = model
        self._client = client or AsyncOpenAI(api_key=api_key, max_retries=2, timeout=60.0)

    async def complete(
        self,
        messages: list[AgentMessage],
        tools: list[OpenAIToolDef],
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        if on_delta is not None:
            return await self._complete_streaming(messages, tools, on_delta)
        res = await self._client.chat.completions.create(
            model=self.model,
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
        res_usage = getattr(res, "usage", None)
        usage = None
        if res_usage is not None and res_usage.prompt_tokens is not None:
            usage = TokenUsage(
                prompt_tokens=res_usage.prompt_tokens,
                completion_tokens=res_usage.completion_tokens or 0,
            )
        return LLMResponse(
            content=msg.content if msg else None,
            tool_calls=tool_calls,
            usage=usage,
        )

    async def _complete_streaming(
        self,
        messages: list[AgentMessage],
        tools: list[OpenAIToolDef],
        on_delta: Callable[[str], None],
    ) -> LLMResponse:
        """``stream=True`` variant — content deltas reach ``on_delta`` as
        they arrive; streamed tool-call fragments are reassembled into
        the same ``LLMResponse`` the non-streaming path returns. Usage
        comes from the trailing chunk (``include_usage``)."""
        stream = await self._client.chat.completions.create(
            model=self.model,
            messages=[_to_openai_message(m) for m in messages],
            tools=cast(list[ChatCompletionToolParam], tools),
            tool_choice="auto",
            stream=True,
            stream_options={"include_usage": True},
        )
        content_parts: list[str] = []
        # Streamed tool calls arrive fragmented by index: id+name on the
        # first fragment, arguments split across later chunks.
        call_parts: dict[int, dict[str, Any]] = {}
        usage: TokenUsage | None = None
        async for chunk in stream:
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None and chunk_usage.prompt_tokens is not None:
                usage = TokenUsage(
                    prompt_tokens=chunk_usage.prompt_tokens,
                    completion_tokens=chunk_usage.completion_tokens or 0,
                )
            for choice in chunk.choices or []:
                delta = choice.delta
                if delta is None:
                    continue
                if delta.content:
                    content_parts.append(delta.content)
                    on_delta(delta.content)
                for tc in delta.tool_calls or []:
                    slot = call_parts.setdefault(tc.index, {"id": "", "name": "", "arguments": []})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function is not None:
                        if tc.function.name:
                            slot["name"] += tc.function.name
                        if tc.function.arguments:
                            slot["arguments"].append(tc.function.arguments)
        tool_calls = [
            ToolCallDef(
                id=slot["id"],
                name=slot["name"],
                arguments="".join(slot["arguments"]),
            )
            for _, slot in sorted(call_parts.items())
        ]
        return LLMResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            usage=usage,
        )


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
