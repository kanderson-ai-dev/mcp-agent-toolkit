"""CLI unit tests — arg parsing, stream reconfiguration, exit codes,
and the full ``_run`` path (real stdio spawn, stub LLM, temp sandbox)."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from scripts.seed_db import seed_demo_db

from mcp_toolkit.client import cli
from mcp_toolkit.config import get_settings
from tests.helpers import copy_promoted_baseline


@pytest.fixture()
def _isolated_env() -> Iterator[None]:
    """``_run`` calls ``load_env_file`` and the process-wide ``get_settings``
    cache: keep each spawning test from reading stale settings and keep any
    ``.env``-injected variables from leaking into the rest of the session."""
    before = set(os.environ)
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()
        for key in set(os.environ) - before:
            os.environ.pop(key, None)


def test_parse_args_flags_and_question() -> None:
    args = cli._parse_args(["--stub", "--json", "what is", "mcp?"])
    assert args.stub is True
    assert args.json is True
    assert args.question == ["what is", "mcp?"]


def test_utf8_streams_is_idempotent() -> None:
    cli._utf8_streams()
    cli._utf8_streams()


async def test_run_empty_prompt_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    """argparse owns the missing-question path: usage on stderr, exit 2."""
    with pytest.raises(SystemExit) as exc:
        await cli._run([])
    assert exc.value.code == 2
    assert "usage" in capsys.readouterr().err


def test_main_propagates_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(argv: list[str]) -> int:
        return 7

    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setattr(sys, "argv", ["cli", "q"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 7


def test_main_fatal_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def boom(argv: list[str]) -> int:
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(cli, "_run", boom)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "fatal: spawn failed" in capsys.readouterr().err


async def test_run_full_path_stub_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    _isolated_env: None,
) -> None:
    """The real CLI path end to end: env → settings → stdio spawn →
    StubLLM run → JSON events on stdout → exit 0.

    ``capfd`` (fd-level) not ``capsys``: the stdio spawn hands
    ``sys.stderr`` to ``subprocess``, which needs a real fileno."""
    registry_root = copy_promoted_baseline(Path("prompts"), tmp_path / "prompts")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "welcome.txt").write_text("sandbox fixture\n")
    db = tmp_path / "demo.db"
    seed_demo_db(db)
    monkeypatch.setenv("SANDBOX_ROOT", str(sandbox))
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("PROMPT_REGISTRY_DIR", str(registry_root))
    monkeypatch.setenv("PROMPT_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("METRICS_PORT", "")
    monkeypatch.setenv("SEARCH_PROVIDER", "none")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "100000")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")

    code = await cli._run(
        ["--stub", "--json", "Which reports in the catalog cover the topic of security?"]
    )
    assert code == 0
    events = [
        json.loads(line) for line in capfd.readouterr().out.splitlines() if line.startswith("{")
    ]
    types = [e["type"] for e in events]
    assert "final" in types
    assert any(e["type"] == "tool_result" and e["tool"] == "db_query" for e in events)
    # JSON mode surfaces token-level deltas; their concatenation equals
    # the final event's content.
    deltas = [e["content"] for e in events if e["type"] == "answer_delta"]
    final = next(e for e in events if e["type"] == "final")
    assert len(deltas) > 1
    assert "".join(deltas) == final["content"]


async def test_run_nonzero_exit_on_iteration_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_env: None
) -> None:
    """terminated_by != "answer" maps to exit 1."""
    registry_root = copy_promoted_baseline(Path("prompts"), tmp_path / "prompts")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "welcome.txt").write_text("x\n")
    db = tmp_path / "demo.db"
    seed_demo_db(db)
    monkeypatch.setenv("SANDBOX_ROOT", str(sandbox))
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("PROMPT_REGISTRY_DIR", str(registry_root))
    monkeypatch.setenv("PROMPT_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("SEARCH_PROVIDER", "none")
    monkeypatch.setenv("METRICS_PORT", "")
    # The er-01 prompt needs three rounds (error → retry → answer); a
    # 2-round cap ends it with iteration_limit → exit 1.
    monkeypatch.setenv("MAX_TOOL_ITERATIONS", "2")
    code = await cli._run(
        [
            "--stub",
            "List the reports ordered by publication date, newest first, "
            "and include each one's citation count.",
        ]
    )
    assert code == 1
