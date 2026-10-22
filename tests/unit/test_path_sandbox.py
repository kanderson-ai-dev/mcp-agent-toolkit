"""Path-sandbox tests — ported from the oracle's path-sandbox.test.ts
plus the traversal matrix required by the plan."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from mcp_toolkit.result import ToolError
from mcp_toolkit.server.guardrails.path_sandbox import (
    realpath_in_sandbox,
    resolve_in_sandbox,
)


@pytest.fixture()
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "sandbox"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "ok.txt").write_text("inside", encoding="utf-8")
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    return root, outside


def _make_dir_link(target: Path, link: Path) -> None:
    """Create a directory link without privileges when possible:
    symlink first, then a Windows junction via cmd mklink /J."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError:
        pass
    if os.name == "nt":
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
        )
        if proc.returncode == 0:
            return
    pytest.skip("symlink/junction creation unavailable on this platform")


class TestResolve:
    def test_simple_relative_paths(self, dirs: tuple[Path, Path]) -> None:
        root, _ = dirs
        assert resolve_in_sandbox(root, "ok.txt") == root / "ok.txt"
        assert resolve_in_sandbox(root, "a/b/c.txt") == root / "a" / "b" / "c.txt"

    def test_absolute_paths_inside_root(self, dirs: tuple[Path, Path]) -> None:
        root, _ = dirs
        abs_path = str((root / "ok.txt").resolve())
        assert resolve_in_sandbox(root, abs_path) == root / "ok.txt"

    @pytest.mark.parametrize(
        "rel",
        [
            "../escape.txt",
            "../../etc/passwd",
            "..",
            "..\\..\\windows\\system32\\drivers\\etc\\hosts",
            "a/../../b.txt",
            "..\\escape.txt",
        ],
    )
    def test_traversal_rejected(self, dirs: tuple[Path, Path], rel: str) -> None:
        root, _ = dirs
        with pytest.raises(ToolError) as exc:
            resolve_in_sandbox(root, rel)
        assert exc.value.code == "forbidden"

    def test_dot_resolves_to_root(self, dirs: tuple[Path, Path]) -> None:
        root, _ = dirs
        assert resolve_in_sandbox(root, ".") == root

    @pytest.mark.parametrize(
        "rel",
        [
            "/etc/passwd",
            "/nonexistent/escape",
            "C:\\Windows\\System32\\drivers\\etc\\hosts",
            "C:/Windows/System32/drivers/etc/hosts",
            "\\\\evil-share\\secret.txt",
        ],
    )
    def test_absolute_outside_root_rejected(self, dirs: tuple[Path, Path], rel: str) -> None:
        """Foreign-style absolutes (POSIX on Windows, Windows on POSIX)
        must be rejected on every platform — not only where they parse
        as absolute."""
        root, outside = dirs
        with pytest.raises(ToolError):
            resolve_in_sandbox(root, rel)
        with pytest.raises(ToolError):
            resolve_in_sandbox(root, str(outside.resolve()))

    def test_nul_byte_rejected(self, dirs: tuple[Path, Path]) -> None:
        root, _ = dirs
        with pytest.raises(ToolError) as exc:
            resolve_in_sandbox(root, "a\x00b.txt")
        assert exc.value.code == "invalid_input"

    def test_percent_encoded_literal_is_inert_filename(self, dirs: tuple[Path, Path]) -> None:
        """%2e%2e is a literal filename, not a decoded traversal — the
        sandbox never URL-decodes, so it resolves inside harmlessly."""
        root, _ = dirs
        assert resolve_in_sandbox(root, "%2e%2e") == root / "%2e%2e"

    def test_long_paths_inside_root_resolve(self, dirs: tuple[Path, Path]) -> None:
        root, _ = dirs
        long_name = "a" * 180 + ".txt"
        assert resolve_in_sandbox(root, long_name) == root / long_name


class TestRealpath:
    def test_canonicalizes_existing_files(self, dirs: tuple[Path, Path]) -> None:
        root, _ = dirs
        real = realpath_in_sandbox(root, root / "ok.txt")
        assert real.exists()

    def test_allows_writes_to_nonexistent_files_inside(self, dirs: tuple[Path, Path]) -> None:
        root, _ = dirs
        target = root / "new" / "deep" / "file.txt"
        real = realpath_in_sandbox(root, target)
        assert str(real).startswith(str(Path(os.path.realpath(root))))

    def test_symlink_junction_escape_rejected(self, dirs: tuple[Path, Path]) -> None:
        root, outside = dirs
        link = root / "escape-link"
        _make_dir_link(outside, link)
        target = link / "secret.txt"
        # Lexically inside the sandbox…
        assert resolve_in_sandbox(root, os.path.join("escape-link", "secret.txt")) == target
        # …but canonical resolution reveals the escape → rejected.
        with pytest.raises(ToolError) as exc:
            realpath_in_sandbox(root, target)
        assert exc.value.code == "forbidden"
