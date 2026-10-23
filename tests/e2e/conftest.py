"""E2E fixtures — boot the production frontend build behind the real
FastAPI backend on a free port, with the deterministic offline stack
(``LLM_PROVIDER=stub``, ``SEARCH_PROVIDER=none``). The backend itself
spawns the real MCP server over stdio, so the whole path is exercised;
teardown kills the process tree.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
VIDEO_DIR = ROOT / "test-results" / "videos"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _wait_ready(
    port: int, proc: subprocess.Popen[str], log_path: Path, timeout_s: float = 60
) -> None:
    deadline = time.monotonic() + timeout_s
    url = f"http://127.0.0.1:{port}/api/health"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
            raise RuntimeError(f"web server exited early (code {proc.returncode}):\n{out}")
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.25)
    raise TimeoutError(f"web server on :{port} did not become ready in {timeout_s}s")


def _ensure_build() -> None:
    if not (FRONTEND / "dist" / "index.html").exists():
        subprocess.run(
            ["npm", "run", "build"],
            cwd=FRONTEND,
            check=True,
            timeout=300,
        )


def _boot_server(log_name: str, extra_env: dict[str, str]) -> Iterator[str]:
    """Boot ``mcp-toolkit-web`` with the production build + offline stack.

    Server output goes to a log file, not a pipe — the process logs every
    tool invocation to stderr and an undrained pipe buffer would deadlock
    the event loop mid-suite."""
    _ensure_build()
    port = _free_port()
    log_path = VIDEO_DIR.parent / log_name
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "LLM_PROVIDER": "stub",
            "SEARCH_PROVIDER": "none",
            "WEB_PORT": str(port),
            "METRICS_PORT": "",
            "LANGCHAIN_TRACING_V2": "false",
        }
    )
    env.update(extra_env)
    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "mcp_toolkit.web"],
            env=env,
            cwd=ROOT,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            _wait_ready(port, proc, log_path)
            yield f"http://127.0.0.1:{port}"
        finally:
            _kill_tree(proc)


@pytest.fixture(scope="session")
def console_url() -> Iterator[str]:
    """The standard console server — offline stack, admin mutations off."""
    yield from _boot_server("console-server.log", {})


@pytest.fixture(scope="session")
def admin_console_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Admin-enabled server over an isolated registry copy — mutations
    (candidates, A/B runs, promote, rollback) must never touch the real
    ``prompts/`` tree or the real audit log."""
    tmp = tmp_path_factory.mktemp("registry-e2e")
    shutil.copytree(ROOT / "prompts", tmp / "prompts")
    yield from _boot_server(
        "console-server-admin.log",
        {
            "ENABLE_PROMPT_ADMIN": "1",
            "PROMPT_REGISTRY_DIR": str(tmp / "prompts"),
            "PROMPT_AUDIT_PATH": str(tmp / "audit.jsonl"),
            "PROMPT_AB_RESULTS_DIR": str(tmp / "ab"),
        },
    )


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    """Record video of every run — the raw source for docs/web-demo.gif
    (converted by scripts/make_demo_gif.py; raw video is never committed)."""
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    return {
        **browser_context_args,
        "record_video_dir": str(VIDEO_DIR),
        "record_video_size": {"width": 1280, "height": 800},
        "viewport": {"width": 1280, "height": 800},
        "ignore_https_errors": True,
    }
