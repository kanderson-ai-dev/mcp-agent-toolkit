"""Filesystem sandbox. Two complementary checks:

1. :func:`resolve_in_sandbox` — lexical containment after normalization
   catches ``..`` traversal and absolute paths outside the root.
2. :func:`realpath_in_sandbox` — canonical-path containment catches
   symlink/junction escapes (a lexical check alone is fooled by them).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from mcp_toolkit.result import ToolError

# Windows-style absolute paths (drive letter or UNC share). Rejected
# unconditionally: on POSIX they'd slip through normalization as inert
# filenames, but the same code runs on Windows where they're escapes.
_FOREIGN_ABSOLUTE = re.compile(r"^(?:[a-zA-Z]:[\\/]|\\\\)")


def _norm(path: str | Path) -> str:
    """os.path.normcase — case-insensitive comparison on Windows, no-op
    on POSIX (mirrors the oracle's platform check)."""
    return os.path.normcase(str(path))


def _inside(root: Path, candidate: Path) -> bool:
    try:
        rel = os.path.relpath(_norm(candidate), _norm(root))
    except ValueError:
        # Different drives/mounts (e.g. a UNC share vs C:\root) → outside.
        return False
    return rel == "." or (not rel.startswith("..") and not os.path.isabs(rel))


def resolve_in_sandbox(root: str | Path, rel: str) -> Path:
    """Resolve ``rel`` against ``root``, rejecting anything that escapes it.

    Rejects NUL bytes, Windows-style absolutes on any OS (drive letter or
    UNC) and anything whose lexical resolution leaves the root. Host-native
    absolute paths are still allowed when they resolve inside the root.
    """
    if "\x00" in rel:
        raise ToolError("invalid_input", "Path contains a NUL byte", guardrail="path_sandbox")
    root_resolved = Path(os.path.abspath(os.fspath(root)))
    # os.path.join keeps an absolute rel as-is, like the oracle's resolve().
    resolved = Path(os.path.abspath(os.path.join(root_resolved, rel)))
    foreign_absolute = bool(_FOREIGN_ABSOLUTE.match(rel)) and not os.path.isabs(rel)
    if foreign_absolute or not _inside(root_resolved, resolved):
        raise ToolError(
            "forbidden", f"Path escapes the sandbox root: {rel}", guardrail="path_sandbox"
        )
    return resolved


def realpath_in_sandbox(root: str | Path, candidate: str | Path) -> Path:
    """Canonicalize ``candidate`` (which may not exist yet — e.g. a write
    target) by walking up to the deepest existing ancestor, and verify the
    canonical path stays inside ``root``. Returns the canonical path to
    operate on; catches symlink and junction escapes.
    """
    real_root = Path(os.path.realpath(root))
    missing: list[str] = []
    p = Path(candidate)
    while not p.exists():
        parent = p.parent
        if parent == p:
            break
        missing.append(p.name)
        p = parent
    if not p.exists():
        raise ToolError(
            "forbidden", f"Path escapes the sandbox root: {candidate}", guardrail="path_sandbox"
        )
    real_ancestor = Path(os.path.realpath(p))
    if not _inside(real_root, real_ancestor):
        raise ToolError(
            "forbidden", "Path escapes the sandbox root via a link", guardrail="path_sandbox"
        )
    return real_ancestor.joinpath(*reversed(missing))
