"""LLM factory + OpenAI adapter tests — fully offline (injected fake
client, no network)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from mcp_toolkit.client.llm import create_llm
from mcp_toolkit.client.llm.openai_llm import OpenAILLM, _to_openai_message
from mcp_toolkit.client.llm.stub_llm import StubLLM
from mcp_toolkit.client.llm.types import (
    AssistantMessage,
    SystemMessage,
    ToolCallDef,
    ToolMessage,
    UserMessage,
)
from mcp_toolkit.config import Settings
from mcp_toolkit.errors import ConfigError


class TestCreateLLM:
    def test_auto_with_key_uses_openai(self, clean_env: pytest.MonkeyPatch) -> None:
        llm = create_llm(Settings(llm_provider="auto", openai_api_key=SecretStr("sk-x")))
        assert llm.name == "openai"

    def test_auto_without_key_uses_stub(self, clean_env: pytest.MonkeyPatch) -> None:
        llm = create_llm(Settings(llm_provider="auto", openai_api_key=None))
        assert isinstance(llm, StubLLM)

    def test_explicit_stub(self, clean_env: pytest.MonkeyPatch) -> None:
        assert isinstance(create_llm(Settings(llm_provider="stub")), StubLLM)

    def test_openai_without_key_fails_loudly(self, clean_env: pytest.MonkeyPatch) -> None:
        with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
            create_llm(Settings(llm_provider="openai", openai_api_key=None))


class TestMessageConversion:
    def test_system_and_user(self) -> None:
        assert _to_openai_message(SystemMessage(content="s")) == {
            "role": "system",
            "content": "s",
        }
        assert _to_openai_message(UserMessage(content="u")) == {
            "role": "user",
            "content": "u",
        }

    def test_tool_message(self) -> None:
        assert _to_openai_message(ToolMessage(tool_call_id="t1", name="x", content="c")) == {
            "role": "tool",
            "tool_call_id": "t1",
            "content": "c",
        }

    def test_assistant_with_tool_calls(self) -> None:
        msg = _to_openai_message(
            AssistantMessage(
                content=None,
                tool_calls=(ToolCallDef(id="i1", name="n", arguments='{"a":1}'),),
            )
        )
        assert msg["role"] == "assistant"
        assert msg["tool_calls"][0]["function"]["name"] == "n"

    def test_assistant_without_calls(self) -> None:
        msg = _to_openai_message(AssistantMessage(content="plain"))
        assert "tool_calls" not in msg


class _FakeCompletions:
    def __init__(self) -> None:
        self.last: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> Any:
        self.last = kwargs
        call = SimpleNamespace(
            id="call-1",
            type="function",
            function=SimpleNamespace(name="web_search", arguments='{"q":1}'),
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="hi", tool_calls=[call]))]
        )


class TestOpenAILLM:
    async def test_complete_maps_response(self) -> None:
        completions = _FakeCompletions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        llm = OpenAILLM("sk-x", "model-x", client=client)  # type: ignore[arg-type]

        res = await llm.complete(
            [UserMessage(content="q")],
            [{"type": "function", "function": {"name": "web_search"}}],
        )
        assert res.content == "hi"
        assert [c.name for c in res.tool_calls] == ["web_search"]
        assert completions.last["model"] == "model-x"
        assert completions.last["tool_choice"] == "auto"
        assert completions.last["messages"][0]["role"] == "user"

    async def test_complete_no_choices(self) -> None:
        class Empty:
            async def create(self, **kwargs: Any) -> Any:
                return SimpleNamespace(choices=[])

        client = SimpleNamespace(chat=SimpleNamespace(completions=Empty()))
        llm = OpenAILLM("k", "m", client=client)  # type: ignore[arg-type]
        res = await llm.complete([], [])
        assert res.content is None
        assert res.tool_calls == []
