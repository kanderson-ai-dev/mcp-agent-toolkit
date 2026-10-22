"""LLM selection — ``auto`` picks OpenAI when keyed, else the
deterministic stub so the agent is fully runnable offline."""

from __future__ import annotations

from mcp_toolkit.client.llm.stub_llm import StubLLM
from mcp_toolkit.client.llm.types import LLMClient
from mcp_toolkit.config import Settings
from mcp_toolkit.errors import ConfigError

__all__ = ["LLMClient", "create_llm"]


def create_llm(settings: Settings) -> LLMClient:
    from mcp_toolkit.client.llm.openai_llm import OpenAILLM

    match settings.llm_provider:
        case "openai":
            if settings.openai_api_key is None:
                raise ConfigError("LLM_PROVIDER=openai requires OPENAI_API_KEY")
            return OpenAILLM(settings.openai_api_key.get_secret_value(), settings.chat_model_name)
        case "stub":
            return StubLLM()
        case "auto":
            return (
                OpenAILLM(settings.openai_api_key.get_secret_value(), settings.chat_model_name)
                if settings.openai_api_key is not None
                else StubLLM()
            )
