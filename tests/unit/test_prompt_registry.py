"""Prompt Registry — schema, state machine, single-promoted invariant,
atomic candidate creation, diff and rollback. Everything runs against
temp dirs; the committed ``prompts/`` artifacts are only read for the
golden-parity test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from mcp_toolkit.prompts.errors import (
    InvalidTransition,
    RegistryValidationError,
    UnknownPromptVersion,
)
from mcp_toolkit.prompts.registry import PromptRegistry
from mcp_toolkit.prompts.schema import PromptArtifact, load_artifact

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "prompts"
TEMPLATE_V1 = "You are prompt v1.\nAnswer carefully."
TEMPLATE_V2 = "You are prompt v2.\nAnswer very carefully."


def _artifact(name: str, version: int, status: str, template: str) -> dict[str, object]:
    return {
        "name": name,
        "version": version,
        "status": status,
        "author": "tests",
        "created_at": "2026-01-01T00:00:00+00:00",
        "description": f"{name} v{version}",
        "template": template,
    }


def _write_artifact(root: Path, name: str, version: int, status: str, template: str) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"v{version}.yaml").write_text(
        yaml.safe_dump(_artifact(name, version, status, template)), encoding="utf-8"
    )


@pytest.fixture()
def registry(tmp_path: Path) -> PromptRegistry:
    _write_artifact(tmp_path, "agent_system", 1, "promoted", TEMPLATE_V1)
    _write_artifact(tmp_path, "judge_rubric", 1, "promoted", "Judge strictly.")
    return PromptRegistry(tmp_path, audit_path=tmp_path / "audit.jsonl")


# ── schema ────────────────────────────────────────────────────────────


def test_schema_rejects_extra_fields() -> None:
    data = _artifact("agent_system", 1, "promoted", "x")
    data["unexpected"] = "nope"
    with pytest.raises(ValidationError, match="extra"):
        PromptArtifact.model_validate(data)


def test_schema_rejects_missing_fields() -> None:
    data = _artifact("agent_system", 1, "promoted", "x")
    del data["template"]
    with pytest.raises(ValidationError):
        PromptArtifact.model_validate(data)


def test_schema_rejects_bad_status_and_version() -> None:
    for bad in ("live", "PROMOTED", "old"):
        with pytest.raises(ValidationError):
            PromptArtifact.model_validate(_artifact("agent_system", 1, bad, "x"))
    with pytest.raises(ValidationError):
        PromptArtifact.model_validate(_artifact("agent_system", 0, "promoted", "x"))


def test_load_artifact_rejects_non_mapping(tmp_path: Path) -> None:
    p = tmp_path / "v1.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_artifact(p)


# ── validation at load ────────────────────────────────────────────────


def test_two_promoted_versions_fail_startup(tmp_path: Path) -> None:
    _write_artifact(tmp_path, "agent_system", 1, "promoted", "a")
    _write_artifact(tmp_path, "agent_system", 2, "promoted", "b")
    with pytest.raises(RegistryValidationError, match="exactly 1 promoted"):
        PromptRegistry(tmp_path, audit_path=tmp_path / "a.jsonl")


def test_zero_promoted_versions_fail_startup(tmp_path: Path) -> None:
    _write_artifact(tmp_path, "agent_system", 1, "draft", "a")
    with pytest.raises(RegistryValidationError, match="exactly 1 promoted"):
        PromptRegistry(tmp_path, audit_path=tmp_path / "a.jsonl")


def test_name_dir_mismatch_fails(tmp_path: Path) -> None:
    d = tmp_path / "agent_system"
    d.mkdir(parents=True)
    (d / "v1.yaml").write_text(
        yaml.safe_dump(_artifact("wrong_name", 1, "promoted", "x")), encoding="utf-8"
    )
    with pytest.raises(RegistryValidationError, match="!= directory"):
        PromptRegistry(tmp_path, audit_path=tmp_path / "a.jsonl")


def test_filename_version_mismatch_fails(tmp_path: Path) -> None:
    d = tmp_path / "agent_system"
    d.mkdir(parents=True)
    (d / "v9.yaml").write_text(
        yaml.safe_dump(_artifact("agent_system", 1, "promoted", "x")), encoding="utf-8"
    )
    with pytest.raises(RegistryValidationError, match="!= filename"):
        PromptRegistry(tmp_path, audit_path=tmp_path / "a.jsonl")


def test_invalid_yaml_fails_loudly(tmp_path: Path) -> None:
    d = tmp_path / "agent_system"
    d.mkdir(parents=True)
    (d / "v1.yaml").write_text("name: [unclosed", encoding="utf-8")
    with pytest.raises(RegistryValidationError):
        PromptRegistry(tmp_path, audit_path=tmp_path / "a.jsonl")


# ── reads ─────────────────────────────────────────────────────────────


def test_list_get_get_promoted(registry: PromptRegistry) -> None:
    assert {a.name for a in registry.list()} == {"agent_system", "judge_rubric"}
    assert registry.get("agent_system", 1).template == TEMPLATE_V1
    assert registry.get_promoted("agent_system").version == 1
    with pytest.raises(UnknownPromptVersion, match="v99"):
        registry.get("agent_system", 99)
    with pytest.raises(UnknownPromptVersion):
        registry.get("nope", 1)


def test_resolve_specs(registry: PromptRegistry) -> None:
    assert registry.resolve("agent_system", "latest-promoted").version == 1
    assert registry.resolve("agent_system", "1").version == 1
    assert registry.resolve("agent_system", "promoted").version == 1
    with pytest.raises(UnknownPromptVersion, match="v7"):
        registry.resolve("agent_system", "7")
    with pytest.raises(UnknownPromptVersion, match="invalid"):
        registry.resolve("agent_system", "banana")


# ── state machine ─────────────────────────────────────────────────────


def test_create_candidate_writes_v2_and_audits(registry: PromptRegistry) -> None:
    art = registry.create_candidate(
        "agent_system",
        template=TEMPLATE_V2,
        description="try harder",
        author="tests",
    )
    assert art.version == 2 and art.status == "candidate"
    on_disk = load_artifact(registry.root / "agent_system" / "v2.yaml")
    assert on_disk.template == TEMPLATE_V2
    assert on_disk.status == "candidate"
    events = registry.audit_log.read()
    assert events[-1]["type"] == "candidate_created"
    assert events[-1]["version"] == 2
    assert events[-1]["sha256"] == art.sha256


def test_illegal_transitions_rejected(registry: PromptRegistry) -> None:
    # From promoted, only rolled_back is legal; draft/candidate are not.
    for to in ("draft", "candidate"):
        with pytest.raises(InvalidTransition, match="not.*allowed|allowed"):
            registry.transition("agent_system", 1, to)  # type: ignore[arg-type]
    cand = registry.create_candidate(
        "agent_system", template=TEMPLATE_V2, description="d", author="t"
    )
    with pytest.raises(InvalidTransition):
        registry.transition("agent_system", cand.version, "draft")


def test_promote_demotes_incumbent_atomically(registry: PromptRegistry) -> None:
    cand = registry.create_candidate(
        "agent_system", template=TEMPLATE_V2, description="d", author="t"
    )
    registry.promote("agent_system", cand.version, reason="gate passed")
    assert registry.get("agent_system", 1).status == "rolled_back"
    assert registry.get("agent_system", cand.version).status == "promoted"
    assert registry.get_promoted("agent_system").version == cand.version
    # Reloading from disk must show the same consistent state.
    reloaded = PromptRegistry(registry.root, audit_path=registry.audit_log.path)
    assert reloaded.get_promoted("agent_system").version == cand.version
    types = [e["type"] for e in registry.audit_log.read()]
    assert types[-1] == "promoted"


def test_rolled_back_can_return_to_candidate(registry: PromptRegistry) -> None:
    cand = registry.create_candidate(
        "agent_system", template=TEMPLATE_V2, description="d", author="t"
    )
    registry.transition("agent_system", cand.version, "rolled_back")
    out = registry.transition("agent_system", cand.version, "candidate")
    assert out.status == "candidate"


def test_rollback_restores_prior_version(registry: PromptRegistry) -> None:
    cand = registry.create_candidate(
        "agent_system", template=TEMPLATE_V2, description="d", author="t"
    )
    registry.promote("agent_system", cand.version, reason="r")
    restored = registry.rollback("agent_system", 1, reason="regression in v2")
    assert restored.status == "promoted"
    assert registry.get("agent_system", cand.version).status == "rolled_back"
    assert registry.get_promoted("agent_system").version == 1
    types = [e["type"] for e in registry.audit_log.read()]
    assert types[-2:] == ["rolled_back", "promoted"]
    assert registry.audit_log.read()[-1]["previous_promoted"] == cand.version


def test_rollback_requires_reason_and_real_target(registry: PromptRegistry) -> None:
    with pytest.raises(ValueError, match="reason"):
        registry.rollback("agent_system", 1, reason="  ")
    cand = registry.create_candidate(
        "agent_system", template=TEMPLATE_V2, description="d", author="t"
    )
    with pytest.raises(InvalidTransition, match="candidate"):
        registry.rollback("agent_system", cand.version, reason="never promoted")


def test_transition_cannot_orphan_the_promoted_version(registry: PromptRegistry) -> None:
    """``transition(promoted → rolled_back)`` would leave zero promoted —
    the name is unloadable on the next start. Only rollback() and a
    competing promotion may move a version out of ``promoted``."""
    promoted = registry.get_promoted("agent_system")
    with pytest.raises(InvalidTransition, match="promoted"):
        registry.transition("agent_system", promoted.version, "rolled_back", reason="x")
    assert registry.get_promoted("agent_system").version == promoted.version
    # Disk was never touched: a fresh registry loads the same state.
    reloaded = PromptRegistry(registry.root, audit_path=registry.audit_log.path)
    assert reloaded.get_promoted("agent_system").version == promoted.version


def test_candidate_needs_an_existing_lineage(registry: PromptRegistry) -> None:
    """A candidate-only line would leave zero promoted versions for that
    name on the next load — create it by hand first instead."""
    with pytest.raises(UnknownPromptVersion, match="unknown prompt"):
        registry.create_candidate("typo_name", template="x", description="d", author="t")
    assert "typo_name" not in registry._artifacts
    assert not (registry.root / "typo_name").exists()
    # The registry itself stays consistent and reloadable.
    PromptRegistry(registry.root, audit_path=registry.audit_log.path)


def test_failed_candidate_leaves_nothing_behind(registry: PromptRegistry) -> None:
    """Schema rejection happens before any mutation — no version bump,
    no file, no map entry."""
    before = [(a.name, a.version, a.status) for a in registry.list()]
    with pytest.raises(ValidationError):
        registry.create_candidate(
            "agent_system",
            template="x" * 33_000,  # over the 32 000-char schema cap
            description="d",
            author="t",
        )
    assert [(a.name, a.version, a.status) for a in registry.list()] == before
    assert not (registry.root / "agent_system" / "v2.yaml").exists()


def test_failed_promotion_write_keeps_memory_consistent_with_disk(
    registry: PromptRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the candidate's write fails after the demotion write succeeded,
    the in-memory map must not run ahead of the YAML on disk."""
    cand = registry.create_candidate(
        "agent_system", template=TEMPLATE_V2, description="d", author="t"
    )
    real_write = PromptRegistry._write
    calls: list[int] = []

    def flaky(self: PromptRegistry, artifact: PromptArtifact) -> None:
        calls.append(artifact.version)
        if len(calls) == 2:  # demotion written, promotion fails
            raise OSError("simulated write failure")
        real_write(self, artifact)

    monkeypatch.setattr(PromptRegistry, "_write", flaky)
    with pytest.raises(OSError, match="simulated"):
        registry.promote("agent_system", cand.version, reason="r")
    # Memory must not claim a state the disk does not have.
    assert registry.get("agent_system", 1).status == "promoted"
    assert registry.get("agent_system", cand.version).status == "candidate"


def test_promote_override_requires_reason_and_audits(registry: PromptRegistry) -> None:
    cand = registry.create_candidate(
        "agent_system", template=TEMPLATE_V2, description="d", author="t"
    )
    with pytest.raises(ValueError, match="reason"):
        registry.promote_override("agent_system", cand.version, reason="")
    registry.promote_override("agent_system", cand.version, reason="hotfix approved by owner")
    types = [e["type"] for e in registry.audit_log.read()]
    assert types[-1] == "promoted_override"


def test_diff_between_versions(registry: PromptRegistry) -> None:
    cand = registry.create_candidate(
        "agent_system", template=TEMPLATE_V2, description="d", author="t"
    )
    diff = registry.diff("agent_system", 1, cand.version)
    assert "--- agent_system/v1" in diff
    assert f"+++ agent_system/v{cand.version}" in diff
    assert "-You are prompt v1." in diff
    assert "+You are prompt v2." in diff


# ── golden parity: v1 artifacts are the byte-for-byte migration ───────


def test_committed_v1_artifacts_match_legacy_constants() -> None:
    registry = PromptRegistry(REPO_ROOT / "prompts", audit_path=REPO_ROOT / "nope.jsonl")
    for name in ("agent_system", "judge_rubric"):
        expected = (FIXTURES / f"{name}_v1.txt").read_text(encoding="utf-8")
        artifact = registry.get(name, 1)
        # v1 may be rolled_back after a later real promotion — what is
        # pinned is its *content* and that the name still has exactly one
        # promoted version, not v1's own lifecycle state.
        assert artifact.status in {"promoted", "rolled_back"}
        assert artifact.template == expected, f"{name} v1 drifted from the legacy constant"
        assert sum(1 for v in registry.list(name) if v.status == "promoted") == 1


def test_artifact_sha256_is_template_fingerprint(registry: PromptRegistry) -> None:
    import hashlib

    art = registry.get("agent_system", 1)
    assert art.sha256 == hashlib.sha256(TEMPLATE_V1.encode()).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", art.sha256)
