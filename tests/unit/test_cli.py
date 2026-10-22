"""CLI unit tests — arg parsing, stream reconfiguration, exit codes."""

from __future__ import annotations

import sys

import pytest

from mcp_toolkit.client import cli


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
