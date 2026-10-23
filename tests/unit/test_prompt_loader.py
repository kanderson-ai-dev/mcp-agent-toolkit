"""Runtime prompt selection — env-var spec resolution, fingerprinting,
fail-fast behaviour, and ``promptVersion`` flowing through run_agent."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml

from mcp_toolkit.client.agent_loop import AgentLoopDeps, run_agent
from mcp_toolkit.client.events import FinalEvent
from mcp_toolkit.client.llm.types import LLMResponse
from mcp_toolkit.client.mcp_client import DiscoveredTool, ToolOutcome
from mcp_toolkit.prompts.errors import RegistryValidationError, UnknownPromptVersion
from mcp_toolkit.prompts.loader import env_var_for, load_prompt, spec_for

TEMPLATE = "You are the promoted prompt.\nDo good work."


def _write(root: Path, name: str, version: int, status: str, template: str) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"v{version}.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "version": version,
                "status": status,
                "author": "t",
                "created_at": "2026-01-01T00:00:00+00:00",
                "description": "d",
                "template": template,
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    _write(tmp_path, "agent_system", 1, "promoted", TEMPLATE)
    _write(tmp_path, "judge_rubric", 1, "promoted", "rubric")
    return tmp_path


def test_env_var_for() -> None:
    assert env_var_for("agent_system") == "PROMPT_VERSION_AGENT_SYSTEM"
    assert env_var_for("judge_rubric") == "PROMPT_VERSION_JUDGE_RUBRIC"


def test_load_prompt_latest_promoted(root: Path) -> None:
    loaded = load_prompt("agent_system", "latest-promoted", root_dir=root)
    assert loaded.version == 1
    assert loaded.template == TEMPLATE
    assert loaded.sha256 == hashlib.sha256(TEMPLATE.encode()).hexdigest()
    assert loaded.ref == {"name": "agent_system", "version": 1, "sha256": loaded.sha256}


def test_load_prompt_pinned_version(root: Path) -> None:
    _write(root, "agent_system", 2, "candidate", "v2 text")
    assert load_prompt("agent_system", "2", root_dir=root).template == "v2 text"


def test_load_prompt_unknown_version_typed_error(root: Path) -> None:
    with pytest.raises(UnknownPromptVersion):
        load_prompt("agent_system", "42", root_dir=root)


def test_spec_precedence(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPT_VERSION_AGENT_SYSTEM", "1")
    assert spec_for("agent_system") == "1"
    assert spec_for("agent_system", "latest-promoted") == "latest-promoted"
    monkeypatch.delenv("PROMPT_VERSION_AGENT_SYSTEM")
    assert spec_for("agent_system") == "latest-promoted"


def test_invalid_registry_fails_fast(tmp_path: Path) -> None:
    _write(tmp_path, "agent_system", 1, "promoted", "a")
    _write(tmp_path, "agent_system", 2, "promoted", "b")
    with pytest.raises(RegistryValidationError):
        load_prompt("agent_system", root_dir=tmp_path)


def test_run_agent_uses_registry_text_and_emits_prompt_version(root: Path) -> None:
    """``run_agent`` sends the registry template as the system message and
    stamps ``promptVersion`` on the final event."""

    class CaptureLLM:
        name = "capture"

        def __init__(self) -> None:
            self.system: str | None = None

        async def complete(self, messages: Any, _tools: Any) -> LLMResponse:
            self.system = str(messages[0].content)
            return LLMResponse(content="done", tool_calls=[])

    class Inv:
        def list_tools(self) -> list[DiscoveredTool]:
            return []

        async def call(self, n: str, a: dict[str, Any], r: str) -> ToolOutcome:
            raise AssertionError("no tools expected")

    loaded = load_prompt("agent_system", root_dir=root)
    llm = CaptureLLM()
    events: list[Any] = []

    async def _go() -> None:
        await run_agent(
            "q?",
            AgentLoopDeps(
                llm=llm,  # type: ignore[arg-type]
                invoker=Inv(),
                tools=[],
                max_iterations=2,
                emit=events.append,
                prompt=loaded,
            ),
        )

    import asyncio

    asyncio.run(_go())
    assert llm.system == TEMPLATE
    final = next(e for e in events if isinstance(e, FinalEvent))
    assert final.prompt_version is not None
    assert final.prompt_version.name == "agent_system"
    assert final.prompt_version.version == 1
    assert final.prompt_version.sha256 == loaded.sha256
    wire = final.model_dump(by_alias=True, mode="json")
    assert wire["promptVersion"]["name"] == "agent_system"
    assert wire["promptVersion"]["version"] == 1
