"""prompts_cli tests — file guard, promote evidence flow, rollback,
audit. The live ``ab`` path is covered indirectly by ``test_ab_test``
(it exercises the same run_ab machinery over fakes)."""

from __future__ import annotations

import json
import shutil
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest
import scripts.prompts_cli as cli

from mcp_toolkit.prompts.errors import InvalidTransition
from mcp_toolkit.prompts.registry import PromptRegistry


def _registry(tmp_path: Path) -> PromptRegistry:
    root = tmp_path / "prompts"
    shutil.copytree(Path("prompts"), root)
    return PromptRegistry(root, audit_path=tmp_path / "audit.jsonl")


def _ns(**kw: Any) -> Namespace:
    return Namespace(**kw)


# ── candidate file guard ──────────────────────────────────────────────


def test_candidate_file_must_live_in_repo(tmp_path: Path) -> None:
    outside = tmp_path / "secret_template.txt"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit, match="inside the repository"):
        cli._resolve_candidate_file(str(outside))


def test_candidate_file_must_exist() -> None:
    with pytest.raises(SystemExit, match="not found"):
        cli._resolve_candidate_file("definitely/missing.txt")


def test_candidate_file_inside_repo_resolves() -> None:
    path = cli._resolve_candidate_file("prompts/agent_system/v1.yaml")
    assert path.is_file()
    assert path.is_relative_to(cli.REPO_ROOT)


# ── list / show / diff / audit ────────────────────────────────────────


def test_list_show_diff_audit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reg = _registry(tmp_path)
    assert cli.cmd_list(reg, _ns()) == 0
    assert cli.cmd_show(reg, _ns(name="agent_system", version=1)) == 0
    assert cli.cmd_diff(reg, _ns(name="agent_system", from_version=1, to_version=1)) == 0
    assert cli.cmd_audit(reg, _ns()) == 0
    out = capsys.readouterr().out
    assert "agent_system" in out and "promoted" in out


def test_audit_detects_tamper(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    reg.create_candidate("agent_system", template="t", description="d", author="t")
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    payload["name"] = "forged"
    lines[0] = json.dumps(payload)
    (tmp_path / "audit.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert cli.cmd_audit(reg, _ns()) == 3


# ── candidate / promote / rollback ────────────────────────────────────


def _with_candidate(reg: PromptRegistry) -> None:
    reg.create_candidate("agent_system", template="v2 template", description="v2", author="t")


def test_candidate_command_creates_v2(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    rc = cli.cmd_candidate(
        reg,
        _ns(
            name="agent_system",
            file="prompts/agent_system/v1.yaml",
            description="copy for test",
            author="tester",
            model_hint=None,
        ),
    )
    assert rc == 0
    assert reg.get("agent_system", 2).status == "candidate"


def _evidence(allowed: bool, mode: str = "live") -> dict[str, Any]:
    return {
        "name": "agent_system",
        "candidateVersion": 2,
        "mode": mode,
        "gatePassed": allowed,
        "promotionAllowed": allowed,
        "regressions": [] if allowed else ["taskSuccess: 5 → 4"],
    }


def test_promote_without_evidence_exits_3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reg = _registry(tmp_path)
    _with_candidate(reg)
    monkeypatch.setattr(cli, "latest_ab_result", lambda name, v, **_kw: None)
    rc = cli.cmd_promote(reg, _ns(name="agent_system", version=2, override_reason=None))
    assert rc == 3
    assert reg.get_promoted("agent_system").version == 1


def test_promote_blocked_on_failed_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reg = _registry(tmp_path)
    _with_candidate(reg)
    monkeypatch.setattr(cli, "latest_ab_result", lambda name, v, **_kw: _evidence(allowed=False))
    rc = cli.cmd_promote(reg, _ns(name="agent_system", version=2, override_reason=None))
    assert rc == 3
    assert reg.get_promoted("agent_system").version == 1


def test_promote_with_passing_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reg = _registry(tmp_path)
    _with_candidate(reg)
    monkeypatch.setattr(cli, "latest_ab_result", lambda name, v, **_kw: _evidence(allowed=True))
    rc = cli.cmd_promote(reg, _ns(name="agent_system", version=2, override_reason=None))
    assert rc == 0
    assert reg.get_promoted("agent_system").version == 2


def test_promote_offline_evidence_still_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An offline smoke result is never promotion evidence even when the
    mechanics 'passed'."""
    reg = _registry(tmp_path)
    _with_candidate(reg)
    monkeypatch.setattr(
        cli,
        "latest_ab_result",
        lambda name, v, **_kw: {
            "name": "agent_system",
            "candidateVersion": 2,
            "mode": "offline-smoke",
            "gatePassed": True,
            "promotionAllowed": False,  # ab_result.to_wire() never allows smoke
            "regressions": [],
        },
    )
    rc = cli.cmd_promote(reg, _ns(name="agent_system", version=2, override_reason=None))
    assert rc == 3


def test_promote_override_audits_reason(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    _with_candidate(reg)
    rc = cli.cmd_promote(
        reg,
        _ns(name="agent_system", version=2, override_reason="owner approved bypass"),
    )
    assert rc == 0
    assert reg.get_promoted("agent_system").version == 2
    overrides = [e for e in reg.audit_log.read() if e["type"] == "promoted_override"]
    assert overrides[0]["override_reason"] == "owner approved bypass"


def test_promote_non_candidate_rejected(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    with pytest.raises(SystemExit):
        cli.cmd_promote(reg, _ns(name="agent_system", version=1, override_reason="x"))


def test_rollback_restores_promoted(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    _with_candidate(reg)
    reg.promote("agent_system", 2, reason="gate")
    rc = cli.cmd_rollback(reg, _ns(name="agent_system", to_version=1, reason="v2 regressed"))
    assert rc == 0
    assert reg.get_promoted("agent_system").version == 1


def test_rollback_to_candidate_rejected(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    _with_candidate(reg)
    with pytest.raises(InvalidTransition):
        cli.cmd_rollback(reg, _ns(name="agent_system", to_version=2, reason="x"))


# ── key-like strings refused everywhere ───────────────────────────────


def test_override_reason_with_key_like_string_refused(tmp_path: Path) -> None:
    """A secret in an audit reason must never reach the append-only log."""
    from mcp_toolkit.prompts.errors import UnsafeAuditReason

    reg = _registry(tmp_path)
    _with_candidate(reg)
    with pytest.raises(UnsafeAuditReason):
        cli.cmd_promote(
            reg,
            _ns(
                name="agent_system",
                version=2,
                override_reason="here is the key sk-abcdefghijklmnop",
            ),
        )
    assert reg.get_promoted("agent_system").version == 1
