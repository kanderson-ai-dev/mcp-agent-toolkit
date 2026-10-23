"""Packaging guards — the image must boot the web adapter, and the web
boot path must not require the top-level ``evaluation`` package.

Regression context: the Dockerfile never copied ``evaluation/`` while
``web.app → prompts_routes → ab_test`` imported it at module level, so
``python -m mcp_toolkit.web`` died on ``ModuleNotFoundError`` inside the
container. These checks fail before the image is ever built again.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO / "Dockerfile"
WEB_DIR = REPO / "src" / "mcp_toolkit" / "web"
SRC = REPO / "src"

# Column-0 only: indented occurrences sit inside functions or
# ``if TYPE_CHECKING:`` blocks and do not run at import time.
TOP_LEVEL_EVAL = re.compile(r"^(?:from|import)\s+evaluation\b", re.M)
TOP_LEVEL_AB_TEST = re.compile(r"^(?:from|import)\s+mcp_toolkit\.prompts\.ab_test\b", re.M)


def _dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def test_dockerfile_ships_everything_the_web_adapter_needs() -> None:
    text = _dockerfile()
    assert "AS frontend" in text and "node:" in text  # console build stage
    assert "COPY evaluation/ evaluation/" in text  # builder stage
    assert "/app/evaluation" in text  # runtime stage
    assert "evaluation/dataset.json" in text  # vite embeds uiExample chips
    assert "/build/frontend/dist /app/frontend/dist" in text  # built console
    # /app is root-owned; prompt-registry writes go to the app-owned /data.
    assert "PROMPT_AUDIT_PATH=/data/prompt-registry/audit.jsonl" in text
    assert "PROMPT_AB_RESULTS_DIR=/data/prompt-registry/ab" in text


def test_web_boot_path_never_imports_the_eval_stack_at_top_level() -> None:
    """Importing ``mcp_toolkit.web.app`` must work without ``evaluation/`` —
    only the lazily-loaded A/B runner may touch it."""
    for path in WEB_DIR.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for pattern in (TOP_LEVEL_EVAL, TOP_LEVEL_AB_TEST):
            match = pattern.search(source)
            assert match is None, f"{path}: {match.group()!r} re-breaks the image boot"


def test_every_top_level_evaluation_import_is_covered_by_the_image() -> None:
    offenders = [
        path
        for path in SRC.rglob("*.py")
        if TOP_LEVEL_EVAL.search(path.read_text(encoding="utf-8"))
    ]
    if offenders:
        assert "COPY evaluation/ evaluation/" in _dockerfile(), offenders


def test_web_bind_address_stays_configurable() -> None:
    source = (WEB_DIR / "__main__.py").read_text(encoding="utf-8")
    assert "settings.web_host" in source
    assert 'host="127.0.0.1"' not in source
