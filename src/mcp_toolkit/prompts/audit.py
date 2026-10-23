"""Append-only, hash-chained audit log for prompt-registry mutations.

Every mutation appends one JSON line::

    {"prevHash": "…", "hash": "…", "type": "promoted", …}

where ``hash = sha256(prevHash + canonical_json(event))`` and
``canonical_json`` sorts keys with compact separators. Editing or
deleting any line breaks the chain — ``verify_chain`` pinpoints the
first tampered line. Reasons are scanned for key-like strings before
writing: a secret must never land in the append-only log.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from mcp_toolkit.prompts.errors import UnsafeAuditReason

GENESIS_HASH = "0" * 64

AUDIT_EVENT_TYPES: tuple[str, ...] = (
    "candidate_created",
    "ab_test",
    "promoted",
    "promoted_override",
    "rolled_back",
)

# Strings that look like credentials — a reason carrying one is refused
# so the audit log can never become a secret store.
_KEY_LIKE_RE = re.compile(
    r"sk-[A-Za-z0-9_-]{8,}"
    r"|sk_live_[A-Za-z0-9]{8,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|Bearer\s+[A-Za-z0-9._~+/=-]{10,}"
    r"|(?:api[_-]?key|secret|password|passwd|token)\s*[:=]\s*\S{8,}",
    re.IGNORECASE,
)


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _check_reason_safety(event: dict[str, Any]) -> None:
    for key in ("reason", "override_reason"):
        value = event.get(key)
        if isinstance(value, str) and _KEY_LIKE_RE.search(value):
            raise UnsafeAuditReason(
                f"audit field '{key}' contains a key-like string — refusing to write"
            )


class AuditLog:
    """JSONL audit chain rooted at ``path``. Thread-safe; the file is
    created lazily on first append."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        """Validate, chain and append ``event``; returns the stored line."""
        event_type = event.get("type")
        if event_type not in AUDIT_EVENT_TYPES:
            raise ValueError(
                f"unknown audit event type {event_type!r} — allowed: {AUDIT_EVENT_TYPES}"
            )
        if "prevHash" in event or "hash" in event:
            raise ValueError("audit events may not carry prevHash/hash — they are computed")
        _check_reason_safety(event)
        with self._lock:
            prev_hash = self._last_hash()
            line = {
                "prevHash": prev_hash,
                "hash": hashlib.sha256(
                    (prev_hash + canonical_json(event)).encode("utf-8")
                ).hexdigest(),
                **event,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
                fh.flush()
            return line

    def read(self) -> list[dict[str, Any]]:
        """All audit lines in order; empty list when the log doesn't exist."""
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def verify_chain(self) -> int | None:
        """Return the 1-based line number of the first tampered entry, or
        ``None`` when the whole chain verifies. An unreadable/invalid line
        also counts as tampered."""
        if not self.path.exists():
            return None
        prev_hash = GENESIS_HASH
        with self.path.open(encoding="utf-8") as fh:
            for n, raw in enumerate(fh, start=1):
                try:
                    line = json.loads(raw)
                    stored_hash = line.pop("hash")
                    stored_prev = line.pop("prevHash")
                except (json.JSONDecodeError, KeyError, AttributeError):
                    return n
                if stored_prev != prev_hash:
                    return n
                if (
                    stored_hash
                    != hashlib.sha256(
                        (prev_hash + canonical_json(line)).encode("utf-8")
                    ).hexdigest()
                ):
                    return n
                prev_hash = stored_hash
        return None

    def _last_hash(self) -> str:
        if not self.path.exists():
            return GENESIS_HASH
        last = ""
        with self.path.open(encoding="utf-8") as fh:
            for raw in fh:
                if raw.strip():
                    last = raw
        if not last:
            return GENESIS_HASH
        try:
            return str(json.loads(last)["hash"])
        except (json.JSONDecodeError, KeyError) as exc:
            raise ValueError(f"audit log tail is corrupted — cannot append to {self.path}") from exc
