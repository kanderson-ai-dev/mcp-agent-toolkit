"""The prompt registry — versioned YAML artifacts under
``prompts/<name>/vN.yaml`` behind a strict state machine.

Invariants, all enforced at load time so an invalid registry fails
startup rather than silently falling back:

- every ``vN.yaml`` validates against :class:`PromptArtifact`
  (``extra="forbid"``) and its filename version matches the body;
- exactly **one** ``promoted`` artifact per prompt name;
- file versions are unique and contiguous-free (gaps allowed, dupes
  rejected).

Mutations are atomic (``tmp`` + ``os.replace``) and serialized behind a
lock, and every one lands in the hash-chained audit log.
"""

from __future__ import annotations

import contextlib
import difflib
import os
import re
import tempfile
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from mcp_toolkit.prompts.audit import AuditLog, check_event_safety
from mcp_toolkit.prompts.errors import (
    InvalidTransition,
    RegistryValidationError,
    UnknownPromptVersion,
)
from mcp_toolkit.prompts.schema import (
    ARTIFACT_FILENAME_RE,
    PromptArtifact,
    PromptStatus,
    dump_artifact,
    load_artifact,
)

# Allowed ``transition()`` moves — no free transitions.
ALLOWED_TRANSITIONS: dict[PromptStatus, tuple[PromptStatus, ...]] = {
    "draft": ("candidate",),
    "candidate": ("promoted", "rolled_back"),
    "promoted": ("rolled_back",),
    "rolled_back": ("candidate",),
}

DEFAULT_AUDIT_PATH = Path("data/prompt-registry/audit.jsonl")


class PromptRegistry:
    """Validated view of a ``prompts/`` directory plus its audit log."""

    def __init__(self, root_dir: Path | str, audit_path: Path | str | None = None):
        self.root = Path(root_dir)
        self.audit_log = AuditLog(
            Path(audit_path) if audit_path is not None else DEFAULT_AUDIT_PATH
        )
        self._lock = threading.Lock()
        self._artifacts: dict[str, dict[int, PromptArtifact]] = self._load_all()
        self._validate()

    # ── loading & validation ────────────────────────────────────────

    def _load_all(self) -> dict[str, dict[int, PromptArtifact]]:
        out: dict[str, dict[int, PromptArtifact]] = {}
        if not self.root.exists():
            return out
        file_re = re.compile(ARTIFACT_FILENAME_RE)
        for name_dir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            versions: dict[int, PromptArtifact] = {}
            for f in sorted(name_dir.glob("v*.yaml")):
                match = file_re.fullmatch(f.name)
                if match is None:
                    raise RegistryValidationError(f"unexpected artifact filename: {f}")
                try:
                    artifact = load_artifact(f)
                except Exception as exc:
                    raise RegistryValidationError(f"{f}: {exc}") from exc
                file_version = int(match.group(1))
                problems = []
                if artifact.name != name_dir.name:
                    problems.append(f"name {artifact.name!r} != directory {name_dir.name!r}")
                if artifact.version != file_version:
                    problems.append(f"version {artifact.version} != filename v{file_version}")
                if artifact.version in versions:
                    problems.append(f"duplicate v{artifact.version}")
                if problems:
                    raise RegistryValidationError(f"{f}: {'; '.join(problems)}")
                versions[artifact.version] = artifact
            if versions:
                out[name_dir.name] = versions
        return out

    def _validate(self) -> None:
        for name, versions in self._artifacts.items():
            promoted = [a for a in versions.values() if a.status == "promoted"]
            if len(promoted) != 1:
                hint = ""
                if not promoted:
                    hint = (
                        " — fix by hand: set `status: promoted` in the "
                        "YAML of the version that should be active"
                    )
                raise RegistryValidationError(
                    f"{name}: expected exactly 1 promoted version, found {len(promoted)}{hint}"
                )

    # ── reads ───────────────────────────────────────────────────────

    def list(self, name: str | None = None) -> list[PromptArtifact]:
        """All artifacts, or just one name's, sorted by version."""
        names: Iterable[str] = [name] if name is not None else self._artifacts
        out: list[PromptArtifact] = []
        for n in names:
            out.extend(self._artifacts.get(n, {}).values())
        return sorted(out, key=lambda a: (a.name, a.version))

    def get(self, name: str, version: int) -> PromptArtifact:
        try:
            return self._artifacts[name][version]
        except KeyError as exc:
            known = sorted(self._artifacts.get(name, {})) or "none"
            raise UnknownPromptVersion(
                f"{name}@v{version} does not exist (known versions: {known})"
            ) from exc

    def get_promoted(self, name: str) -> PromptArtifact:
        for artifact in self._artifacts.get(name, {}).values():
            if artifact.status == "promoted":
                return artifact
        raise UnknownPromptVersion(f"{name}: no promoted version")

    def resolve(self, name: str, spec: str) -> PromptArtifact:
        """Resolve a runtime spec: ``latest-promoted`` or a version number."""
        spec = spec.strip().lower()
        if spec in ("latest-promoted", "promoted", "latest"):
            return self.get_promoted(name)
        try:
            return self.get(name, int(spec))
        except ValueError:
            raise UnknownPromptVersion(
                f"invalid prompt version spec {spec!r} for {name} — "
                "expected 'latest-promoted' or a version number"
            ) from None

    def diff(self, name: str, from_version: int, to_version: int) -> str:
        """Unified diff of two versions' templates."""
        a = self.get(name, from_version)
        b = self.get(name, to_version)
        return "".join(
            difflib.unified_diff(
                a.template.splitlines(keepends=True),
                b.template.splitlines(keepends=True),
                fromfile=f"{name}/v{from_version}",
                tofile=f"{name}/v{to_version}",
            )
        )

    # ── mutations ───────────────────────────────────────────────────

    def create_candidate(
        self,
        name: str,
        *,
        template: str,
        description: str,
        author: str,
        model_hint: str | None = None,
    ) -> PromptArtifact:
        """Write ``vN+1.yaml`` with ``status=candidate`` and audit it."""
        # Safety-check free text before any disk write — an audit
        # rejection must never leave a mutated artifact behind.
        check_event_safety({"description": description})
        with self._lock:
            if name not in self._artifacts:
                # A candidate-only lineage violates the one-promoted
                # invariant at the next load — it must never exist.
                raise UnknownPromptVersion(
                    f"{name}: unknown prompt — a candidate attaches to an existing lineage"
                )
            # Validate before mutating: a schema rejection leaves no map
            # entry, no version bump and no file behind.
            next_version = max(self._artifacts[name]) + 1
            artifact = PromptArtifact(
                name=name,
                version=next_version,
                status="candidate",
                author=author,
                created_at=datetime.now(UTC),
                description=description,
                model_hint=model_hint,
                template=template,
            )
            self._write(artifact)
            self._artifacts[name][next_version] = artifact
            self.audit_log.append(
                {
                    "type": "candidate_created",
                    "name": name,
                    "version": next_version,
                    "author": author,
                    "sha256": artifact.sha256,
                    "description": description,
                }
            )
            return artifact

    def transition(
        self, name: str, version: int, to_status: PromptStatus, *, reason: str = ""
    ) -> PromptArtifact:
        """Apply a single ``ALLOWED_TRANSITIONS`` move.

        Promoting a version atomically rolls the previously promoted one
        back — the one-promoted-per-name invariant is never violated.
        """
        check_event_safety({"reason": reason})
        with self._lock:
            artifact = self.get(name, version)
            allowed = ALLOWED_TRANSITIONS[artifact.status]
            if to_status not in allowed:
                raise InvalidTransition(
                    f"{name}@v{version}: {artifact.status} → {to_status} is not "
                    f"allowed (from {artifact.status}: {sorted(allowed)})"
                )
            if artifact.status == "promoted":
                # The table lists promoted → rolled_back because that move
                # exists *inside* rollback(); a bare transition that
                # orphans the name's promoted version is refused.
                raise InvalidTransition(
                    f"{name}@v{version}: the promoted version cannot leave "
                    "'promoted' directly — promote another version or use "
                    "rollback()"
                )
            demoted: PromptArtifact | None = None
            if to_status == "promoted":
                current = self.get_promoted(name)
                if current.version != version:
                    demoted = current.model_copy(update={"status": "rolled_back"})
            updated = artifact.model_copy(update={"status": to_status})
            # Disk first, memory last: a failed write never leaves the
            # in-memory map ahead of the YAML. A crash between the two
            # writes leaves zero promoted on disk and the next load fails
            # loudly — that window is inherent to two files, by design.
            if demoted is not None:
                self._write(demoted)
            self._write(updated)
            if demoted is not None:
                self._artifacts[name][demoted.version] = demoted
            self._artifacts[name][version] = updated
            if to_status == "promoted":
                self.audit_log.append(
                    {
                        "type": "promoted",
                        "name": name,
                        "version": version,
                        "previous_promoted": demoted.version if demoted else None,
                        "sha256": updated.sha256,
                        "reason": reason,
                    }
                )
            elif to_status == "rolled_back":
                self.audit_log.append(
                    {
                        "type": "rolled_back",
                        "name": name,
                        "version": version,
                        "reason": reason,
                    }
                )
            return updated

    def promote(self, name: str, version: int, *, reason: str = "") -> PromptArtifact:
        """Candidate → promoted (demoting the incumbent)."""
        return self.transition(name, version, "promoted", reason=reason)

    def promote_override(self, name: str, version: int, *, reason: str) -> PromptArtifact:
        """Promotion that bypassed a failed A/B gate — always audited as
        ``promoted_override`` with a mandatory, secret-free reason."""
        if not reason.strip():
            raise ValueError("promote_override requires a non-empty reason")
        check_event_safety({"override_reason": reason})
        updated = self.transition(name, version, "promoted", reason=reason)
        # transition() wrote a `promoted` event — replace semantics matter:
        # record the override explicitly so the audit shows it was a
        # human decision against the gate.
        self.audit_log.append(
            {
                "type": "promoted_override",
                "name": name,
                "version": version,
                "sha256": updated.sha256,
                "override_reason": reason,
            }
        )
        return updated

    def rollback(self, name: str, to_version: int, *, reason: str) -> PromptArtifact:
        """Restore ``to_version`` to promoted, rolling the incumbent back.
        Requires a reason — a rollback without one is unauditable."""
        if not reason.strip():
            raise ValueError("rollback requires a non-empty reason")
        check_event_safety({"reason": reason})
        with self._lock:
            target = self.get(name, to_version)
            if target.status in ("draft", "candidate"):
                raise InvalidTransition(
                    f"{name}@v{to_version}: cannot roll back to status "
                    f"{target.status!r} (must have been promoted before)"
                )
            current = self.get_promoted(name)
            if current.version == to_version:
                return target
            demoted = current.model_copy(update={"status": "rolled_back"})
            restored = target.model_copy(update={"status": "promoted"})
            # Same disk-first ordering as transition(): a failed write
            # never leaves memory ahead of disk.
            self._write(demoted)
            self._write(restored)
            self._artifacts[name][demoted.version] = demoted
            self._artifacts[name][to_version] = restored
            self.audit_log.append(
                {
                    "type": "rolled_back",
                    "name": name,
                    "version": current.version,
                    "reason": reason,
                    "restored_version": to_version,
                }
            )
            self.audit_log.append(
                {
                    "type": "promoted",
                    "name": name,
                    "version": to_version,
                    "previous_promoted": current.version,
                    "sha256": restored.sha256,
                    "reason": f"rollback: {reason}",
                }
            )
            return restored

    # ── internals ───────────────────────────────────────────────────

    def _write(self, artifact: PromptArtifact) -> None:
        """Atomic artifact write: tmp file + ``os.replace``."""
        name_dir = self.root / artifact.name
        name_dir.mkdir(parents=True, exist_ok=True)
        target = name_dir / f"v{artifact.version}.yaml"
        fd, tmp = tempfile.mkstemp(dir=name_dir, prefix=".tmp-", suffix=".yaml")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(dump_artifact(artifact))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def reload(self) -> None:
        """Re-read the registry from disk (picks up external writes)."""
        with self._lock:
            self._artifacts = self._load_all()
            self._validate()
