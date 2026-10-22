"""Record a demo clip of the web console for ``docs/web-demo.gif``.

The e2e suite's per-test recordings finish in ~1s — far too fast for a
demo. This script boots the same offline stack (production build +
``LLM_PROVIDER=stub`` + ``SEARCH_PROVIDER=none`` over the real MCP
stdio transport), then drives the console slowly and deliberately:
the question is typed character by character, tool cards stream in,
and the camera lingers on the rendered final answer.

Usage::

    uv run python -m scripts.record_demo            # → test-results/demo/demo.webm
    uv run python -m scripts.make_demo_gif          # → docs/web-demo.gif

Raw video is never committed — ``test-results/`` is gitignored.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "test-results" / "demo"

QUESTION = (
    "What security reports do we have in the database, and what does the web "
    "say about security in MCP? Save me a combined briefing in "
    "reports/security-briefing.md."
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_ready(port: int, proc: subprocess.Popen[bytes], timeout_s: float = 60) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"web server exited early (code {proc.returncode})")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=2).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.25)
    raise TimeoutError(f"web server on :{port} did not become ready in {timeout_s}s")


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        taskkill = shutil.which("taskkill") or "taskkill"
        subprocess.run(  # noqa: S603 — fixed system utility, fixed args
            [taskkill, "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    if not (ROOT / "frontend" / "dist" / "index.html").exists():
        raise SystemExit("frontend/dist missing — run `npm --prefix frontend run build` first")

    port = _free_port()
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    log_path = DEMO_DIR / "server.log"
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

    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "mcp_toolkit.web"],
            env=env,
            cwd=ROOT,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_ready(port, proc)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                context = browser.new_context(
                    record_video_dir=str(DEMO_DIR),
                    record_video_size={"width": 1280, "height": 800},
                    viewport={"width": 1280, "height": 800},
                    color_scheme="dark",
                )
                page = context.new_page()
                page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
                page.get_by_label("Question for the agent").wait_for(state="visible")
                page.wait_for_timeout(1200)

                composer = page.get_by_label("Question for the agent")
                composer.click()
                composer.press_sequentially(QUESTION, delay=18)
                page.wait_for_timeout(500)
                page.get_by_label("Send question").click()

                page.get_by_role("heading", name="Final answer").wait_for(
                    state="visible", timeout=60_000
                )
                page.wait_for_timeout(1500)
                page.mouse.wheel(0, 500)
                page.wait_for_timeout(1000)
                page.mouse.wheel(0, 500)
                page.wait_for_timeout(1800)

                context.close()
                browser.close()
        finally:
            _kill_tree(proc)

    videos = sorted(DEMO_DIR.glob("*.webm"), key=lambda p: p.stat().st_mtime)
    if not videos:
        raise SystemExit("no demo video produced")
    print(f"demo recorded: {videos[-1]} ({videos[-1].stat().st_size / 1024:.0f} KiB)")
    print("next: uv run python -m scripts.make_demo_gif " + str(videos[-1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
