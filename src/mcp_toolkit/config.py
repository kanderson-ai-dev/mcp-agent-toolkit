"""Typed settings for the whole toolkit.

Env-var names mirror the TypeScript oracle's ``envSchema`` exactly so
``.env`` files are interchangeable across the two repos. Secrets are
``SecretStr`` and empty strings mean *absent* — an unconfigured CI
secret never reads as a real key.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import (
    Field,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core.core_schema import ValidatorFunctionWrapHandler
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every supported environment variable, parsed and validated."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM (agent client) ────────────────────────────────────────────
    openai_api_key: SecretStr | None = None
    chat_model_name: str = "gpt-4o-mini"
    llm_provider: Literal["auto", "openai", "stub"] = "auto"

    # ── web_search tool ───────────────────────────────────────────────
    search_provider: Literal["auto", "tavily", "duckduckgo", "none"] = "auto"
    search_api_key: SecretStr | None = None

    # ── MCP server spawn (agent client) ───────────────────────────────
    mcp_server_command: str | None = None
    mcp_server_args: str | None = None

    # ── Server resources ──────────────────────────────────────────────
    sandbox_root: Path = Path("./data/sandbox")
    db_path: Path = Path("./data/demo.db")

    # ── Guardrails ────────────────────────────────────────────────────
    rate_limit_per_minute: int = Field(default=60, gt=0)
    max_tool_iterations: int = Field(default=8, gt=0, le=50)
    max_tool_output_chars: int = Field(default=12_000, gt=0)
    read_file_max_bytes: int = Field(default=262_144, gt=0)
    write_file_max_bytes: int = Field(default=1_048_576, gt=0)

    # ── Observability ─────────────────────────────────────────────────
    log_level: str = "info"
    metrics_port: int | None = Field(default=None, ge=1, le=65535)
    output_format: Literal["pretty", "json"] = "pretty"

    # ── Run telemetry / budget gate ───────────────────────────────────
    run_budget_usd: float | None = Field(default=None, gt=0)
    """Optional hard cap on accumulated LLM cost per run — when the run's
    cost reaches it, the loop stops with ``terminatedBy: budget_limit``.
    Only enforceable for models with a known price (see pricing.py)."""
    model_pricing_json: str | None = None
    """Optional JSON map overriding the committed price table:
    ``{"model": {"prompt": <usd>, "completion": <usd>}}`` per 1M tokens."""

    # ── Evaluation / LangSmith (all optional) ─────────────────────────
    langchain_tracing_v2: Literal["true", "false"] | None = None
    langchain_api_key: SecretStr | None = None
    langchain_project: str = "mcp-agent-toolkit-py"

    # ── Web console ───────────────────────────────────────────────────
    web_port: int = Field(default=3000, ge=1, le=65535)
    web_cors_origin: str = "http://localhost:5173"

    # ── Prompt Registry ───────────────────────────────────────────────
    prompt_registry_dir: str = "prompts"
    prompt_audit_path: str = "data/prompt-registry/audit.jsonl"
    prompt_ab_results_dir: str = "data/prompt-registry/ab"
    prompt_version_agent_system: str = "latest-promoted"
    prompt_version_judge_rubric: str = "latest-promoted"
    enable_prompt_admin: bool = False

    @field_validator("*", mode="wrap")
    @classmethod
    def _empty_str_is_absent(
        cls, value: Any, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> Any:
        """Empty/whitespace strings fall back to the field default —
        matching the oracle's ``emptyToUndefined`` + zod defaults (so
        ``OPENAI_API_KEY=`` yields ``None`` and ``RATE_LIMIT_PER_MINUTE=``
        yields 60).
        """
        if isinstance(value, str) and value.strip() == "" and info.field_name is not None:
            return cls.model_fields[info.field_name].default
        return handler(value)

    @model_validator(mode="after")
    def _resolve_resource_paths(self) -> Settings:
        """SANDBOX_ROOT and DB_PATH are always absolute paths."""
        self.sandbox_root = self.sandbox_root.resolve()
        self.db_path = self.db_path.resolve()
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings (cached). Tests build ``Settings(...)`` directly."""
    return Settings()


def load_env_file(path: str | Path = ".env") -> None:
    """Load ``.env`` into ``os.environ`` without clobbering real env vars.

    Mirrors the oracle's ``loadEnvFile``: the file is optional and real
    environment variables always win — this exists so the spawned MCP
    server's env allow-list can see ``.env`` values too.
    """
    try:
        values = dotenv_values(path)
    except OSError:
        return
    for key, value in values.items():
        if value is not None:
            os.environ.setdefault(key, value)
