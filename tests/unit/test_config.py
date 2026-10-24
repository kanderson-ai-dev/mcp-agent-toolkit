"""Settings tests — ported from the oracle's config.test.ts."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from mcp_toolkit.config import effective_judge_model, get_settings, load_env_file
from tests.helpers import make_settings

pytestmark = pytest.mark.usefixtures("clean_env")


class TestDefaults:
    def test_safe_defaults_with_empty_env(self) -> None:
        s = make_settings()
        assert s.openai_api_key is None
        assert s.search_api_key is None
        assert s.search_provider == "auto"
        assert s.llm_provider == "auto"
        assert s.chat_model_name == "gpt-4o-mini"
        assert s.rate_limit_per_minute == 60
        assert s.max_tool_iterations == 8
        assert s.max_tool_output_chars == 12_000
        assert s.read_file_max_bytes == 262_144
        assert s.write_file_max_bytes == 1_048_576
        assert s.metrics_port is None
        assert s.output_format == "pretty"
        assert s.web_port == 3000
        assert s.log_level == "info"

    def test_empty_string_secrets_are_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("SEARCH_API_KEY", "   ")
        s = make_settings()
        assert s.openai_api_key is None
        assert s.search_api_key is None

    def test_empty_string_optional_vars_fall_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METRICS_PORT", "")
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "")
        s = make_settings()
        assert s.metrics_port is None
        assert s.rate_limit_per_minute == 60

    def test_real_secret_is_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
        s = make_settings()
        assert s.openai_api_key is not None
        assert s.openai_api_key.get_secret_value() == "sk-test-123"

    def test_numeric_knobs_coerce_from_strings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "12")
        monkeypatch.setenv("MAX_TOOL_ITERATIONS", "3")
        s = make_settings()
        assert s.rate_limit_per_minute == 12
        assert s.max_tool_iterations == 3

    def test_metrics_port_parses_only_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert make_settings().metrics_port is None
        monkeypatch.setenv("METRICS_PORT", "9108")
        assert make_settings().metrics_port == 9108

    def test_sandbox_root_and_db_path_resolve_to_absolute(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        s = make_settings(sandbox_root="./rel/sb", db_path="./rel/demo.db")
        assert s.sandbox_root.is_absolute()
        assert s.db_path.is_absolute()

    def test_invalid_enums_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SEARCH_PROVIDER", "bing")
        with pytest.raises(ValidationError):
            make_settings()
        monkeypatch.delenv("SEARCH_PROVIDER")
        monkeypatch.setenv("LLM_PROVIDER", "nope")
        with pytest.raises(ValidationError):
            make_settings()

    def test_max_tool_iterations_capped_at_50(self) -> None:
        with pytest.raises(ValidationError):
            make_settings(max_tool_iterations=51)


class TestJudgeModel:
    def test_default_falls_back_to_the_agent_model(self) -> None:
        s = make_settings()
        assert s.judge_model_name is None
        assert effective_judge_model(s) == "gpt-4o-mini"

    def test_env_override_grades_with_an_independent_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("JUDGE_MODEL_NAME", "gpt-4o")
        s = make_settings(chat_model_name="agent-m")
        assert effective_judge_model(s) == "gpt-4o"

    def test_empty_env_falls_back_to_the_agent_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JUDGE_MODEL_NAME", "   ")
        s = make_settings()
        assert s.judge_model_name is None
        assert effective_judge_model(s) == s.chat_model_name

    def test_explicit_constructor_value_wins(self) -> None:
        s = make_settings(chat_model_name="agent-m", judge_model_name="judge-m")
        assert effective_judge_model(s) == "judge-m"


class TestSecretHygiene:
    def test_secret_never_in_repr(self) -> None:
        s = make_settings(openai_api_key="sk-live-test")
        assert "sk-live-test" not in repr(s)
        assert "sk-live-test" not in str(s)

    def test_secret_never_in_model_dump(self) -> None:
        s = make_settings(openai_api_key="sk-live-test", search_api_key="tv-x")
        dumped = s.model_dump()
        assert "sk-live-test" not in repr(dumped)
        assert "tv-x" not in repr(dumped)
        json_text = s.model_dump_json()
        assert "sk-live-test" not in json_text


class TestGetSettings:
    def test_get_settings_is_cached(self, clean_env: pytest.MonkeyPatch) -> None:
        get_settings.cache_clear()
        try:
            assert get_settings() is get_settings()
        finally:
            get_settings.cache_clear()


class TestLoadEnvFile:
    def test_absent_file_is_a_noop(self, tmp_path: Path) -> None:
        load_env_file(tmp_path / "missing.env")  # must not raise

    def test_loads_values_and_real_env_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / "x.env"
        env_file.write_text("MCP_TEST_VAR=from-file\nOTHER_VAR=file-val\n")
        monkeypatch.setenv("MCP_TEST_VAR", "from-env")
        monkeypatch.delenv("OTHER_VAR", raising=False)
        load_env_file(env_file)
        assert os.environ["MCP_TEST_VAR"] == "from-env"
        assert os.environ["OTHER_VAR"] == "file-val"
