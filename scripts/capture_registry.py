"""Capture Prompt-Registry-tab screenshots into ``docs/ui-qa/registry/``.

Boots the same offline stack as the e2e suite (production build +
``LLM_PROVIDER=stub`` + ``SEARCH_PROVIDER=none`` over the real MCP stdio
transport) with ``ENABLE_PROMPT_ADMIN=1`` and an isolated registry copy —
candidates/A/B runs/mutations never touch the real ``prompts/`` tree.

Shots produced::

    docs/ui-qa/registry/list.png          version list + status badges
    docs/ui-qa/registry/diff.png          unified diff vs the active version
    docs/ui-qa/registry/ab-blocked.png    gate-failed A/B + blocked promote dialog
    docs/ui-qa/registry/audit.png         audit timeline + "Chain verified"
    docs/ui-qa/registry/mobile-375.png    the tab at 375px
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
OUT = ROOT / "docs" / "ui-qa" / "registry"
WORK = ROOT / "test-results" / "registry-capture"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_ready(port: int, proc: subprocess.Popen[str], log_path: Path) -> None:
    deadline = time.monotonic() + 60
    url = f"http://127.0.0.1:{port}/api/health"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"server exited ({proc.returncode}):\n"
                + log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            )
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.25)
    raise TimeoutError(f"server on :{port} not ready in 60s")


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    registry_copy = WORK / "prompts"
    shutil.rmtree(registry_copy, ignore_errors=True)
    shutil.copytree(ROOT / "prompts", registry_copy)

    port = _free_port()
    log_path = WORK / "server.log"
    env = dict(os.environ)
    env.update(
        {
            "LLM_PROVIDER": "stub",
            "SEARCH_PROVIDER": "none",
            "WEB_PORT": str(port),
            "METRICS_PORT": "",
            "LANGCHAIN_TRACING_V2": "false",
            "ENABLE_PROMPT_ADMIN": "1",
            "PROMPT_REGISTRY_DIR": str(registry_copy),
            "PROMPT_AUDIT_PATH": str(WORK / "audit.jsonl"),
            "PROMPT_AB_RESULTS_DIR": str(WORK / "ab"),
        }
    )
    url = f"http://127.0.0.1:{port}"
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
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_context(
                    viewport={"width": 1280, "height": 860},
                    device_scale_factor=2,
                ).new_page()
                page.goto(url, wait_until="domcontentloaded")
                page.get_by_label("Question for the agent").wait_for(
                    state="visible", timeout=30_000
                )
                page.get_by_role("button", name="Prompt Registry").click()
                page.get_by_role("tab", name="Agent system prompt").wait_for(
                    state="visible", timeout=15_000
                )
                page.screenshot(path=str(OUT / "list.png"), full_page=False)

                # candidate + diff
                page.get_by_role("button", name="New candidate from active").click()
                tpl = page.get_by_label("Template")
                tpl.wait_for(state="visible", timeout=15_000)
                tpl.fill(
                    "You are the mcp-agent-toolkit assistant. Answer with tools "
                    "when they help, keep answers short and cite tool evidence."
                )
                page.get_by_label("Description").fill("Shorter answer style")
                page.get_by_role("button", name="Create candidate").click()
                page.get_by_text("Version 2").first.wait_for(state="visible", timeout=15_000)
                page.get_by_role("button", name="Compare with active").click()
                page.get_by_label("Changes from version 1 to version 2").wait_for(
                    state="visible", timeout=15_000
                )
                page.screenshot(path=str(OUT / "diff.png"), full_page=False)

                # offline A/B → gate-failed result + smoke label
                page.get_by_role("button", name="Smoke check (offline)").click()
                page.get_by_text("Smoke check — not a quality measurement").wait_for(
                    state="visible", timeout=300_000
                )
                page.get_by_role("button", name="Promote to active").click()
                dialog = page.get_by_role("dialog")
                dialog.get_by_role("button", name="Promote", exact=True).click()
                dialog.get_by_text("promotion blocked").wait_for(state="visible", timeout=15_000)
                page.screenshot(path=str(OUT / "ab-blocked.png"), full_page=False)

                # audit (the ab_test event is already on the chain)
                dialog.get_by_role("button", name="Cancel").click()
                audit = page.get_by_text("Chain verified")
                audit.wait_for(state="visible", timeout=15_000)
                audit.scroll_into_view_if_needed()
                page.screenshot(path=str(OUT / "audit.png"), full_page=False)

                # mobile
                mobile = browser.new_context(
                    viewport={"width": 375, "height": 760},
                    device_scale_factor=2,
                ).new_page()
                mobile.goto(url, wait_until="domcontentloaded")
                mobile.get_by_role("button", name="Prompt Registry").click()
                mobile.get_by_role("tab", name="Agent system prompt").wait_for(
                    state="visible", timeout=15_000
                )
                mobile.screenshot(path=str(OUT / "mobile-375.png"), full_page=False)
                browser.close()
        finally:
            if sys.platform == "win32":
                subprocess.run(  # noqa: S603 — PID of our own child
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],  # noqa: S607
                    capture_output=True,
                    check=False,
                )
            else:
                proc.terminate()

    print(f"screenshots -> {OUT}")


if __name__ == "__main__":
    main()
