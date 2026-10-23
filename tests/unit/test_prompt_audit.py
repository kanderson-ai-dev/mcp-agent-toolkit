"""Hash-chained audit log — append, verify, tamper detection, secret
rejection. All against temp files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_toolkit.prompts.audit import GENESIS_HASH, AuditLog, canonical_json
from mcp_toolkit.prompts.errors import UnsafeAuditReason


@pytest.fixture()
def log(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


def _ev(n: int = 1) -> dict[str, object]:
    return {"type": "promoted", "name": "agent_system", "version": n, "reason": "r"}


def test_append_chains_hashes(log: AuditLog) -> None:
    l1 = log.append(_ev(1))
    l2 = log.append(_ev(2))
    assert l1["prevHash"] == GENESIS_HASH
    assert l2["prevHash"] == l1["hash"]
    assert l1["hash"] != l2["hash"]
    lines = log.read()
    assert [line["version"] for line in lines] == [1, 2]


def test_verify_clean_chain(log: AuditLog) -> None:
    for n in (1, 2, 3):
        log.append(_ev(n))
    assert log.verify_chain() is None


def test_verify_detects_tampered_line(log: AuditLog) -> None:
    for n in (1, 2, 3):
        log.append(_ev(n))
    lines = log.path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["reason"] = "edited after the fact"
    lines[1] = json.dumps(tampered)
    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert log.verify_chain() == 2


def test_verify_detects_deleted_line(log: AuditLog) -> None:
    for n in (1, 2, 3):
        log.append(_ev(n))
    lines = log.path.read_text(encoding="utf-8").splitlines()
    del lines[0]
    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert log.verify_chain() == 1


def test_verify_detects_relinked_chain(log: AuditLog) -> None:
    """Rewriting a line's hash to match still breaks prevHash linkage."""
    for n in (1, 2):
        log.append(_ev(n))
    lines = log.path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["version"] = 99
    lines[0] = json.dumps(first)
    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert log.verify_chain() == 1


def test_verify_flags_garbage_line(log: AuditLog) -> None:
    log.append(_ev(1))
    with log.path.open("a", encoding="utf-8") as fh:
        fh.write("not json at all\n")
    assert log.verify_chain() == 2


def test_append_to_corrupted_tail_fails(log: AuditLog) -> None:
    log.path.write_text("garbage\n", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupted"):
        log.append(_ev(1))


@pytest.mark.parametrize(
    "reason",
    [
        "key sk-123456789abcdef leaked",
        "use Bearer abcdefghijklmnop for auth",
        "api_key=abcdef123456789",
        "password: hunter2hunter2",
        "AKIAIOSFODNN7EXAMPLE",
        "-----BEGIN PRIVATE KEY-----",
        "token= xoxb1234567890",
    ],
)
def test_reasons_with_key_like_strings_rejected(log: AuditLog, reason: str) -> None:
    with pytest.raises(UnsafeAuditReason):
        log.append({"type": "promoted", "name": "a", "version": 1, "reason": reason})
    assert not log.path.exists() or not log.read()


def test_override_reason_scanned_too(log: AuditLog) -> None:
    with pytest.raises(UnsafeAuditReason):
        log.append(
            {
                "type": "promoted_override",
                "name": "a",
                "version": 1,
                "override_reason": "needed sk-abcdef12345 quickly",
            }
        )


def test_unknown_event_type_and_reserved_keys_rejected(log: AuditLog) -> None:
    with pytest.raises(ValueError, match="unknown audit event type"):
        log.append({"type": "hacked", "name": "a"})
    with pytest.raises(ValueError, match="prevHash"):
        log.append({"type": "promoted", "name": "a", "prevHash": "f" * 64})


def test_canonical_json_deterministic() -> None:
    assert canonical_json({"b": 1, "a": [2, {"z": None}]}) == '{"a":[2,{"z":null}],"b":1}'


def test_empty_log_verifies(tmp_path: Path) -> None:
    assert AuditLog(tmp_path / "none.jsonl").verify_chain() is None
