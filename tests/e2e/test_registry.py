"""Prompt Registry tab e2e — the offline admin flow end to end.

Runs against an isolated registry copy (``admin_console_url``): create a
candidate, view the diff, run an offline A/B smoke check, get blocked at
promotion, override with a reason, audit the trail and roll back — every
step through the real API and UI. Also asserts the read-only state and
the forbidden-literal sweep on registry surfaces.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_console import _forbidden_hits, _load_console

pytestmark = pytest.mark.e2e


def _open_registry(url: str, page: Page) -> None:
    _load_console(url, page)
    page.get_by_role("button", name="Prompt Registry").click()
    expect(page.get_by_role("tab", name="Agent system prompt")).to_be_visible(timeout=15_000)


def test_registry_admin_off_is_read_only(console_url: str, page: Page) -> None:
    """No ENABLE_PROMPT_ADMIN → read-only notice, zero mutation controls."""
    _open_registry(console_url, page)
    expect(page.get_by_text("Read-only.")).to_be_visible()
    expect(page.get_by_role("button", name="New candidate from active")).to_have_count(0)
    expect(page.get_by_role("button", name=re.compile("Promote"))).to_have_count(0)
    expect(page.get_by_role("button", name=re.compile("Roll back"))).to_have_count(0)
    # Versions still render.
    expect(page.get_by_text("Version 1").first).to_be_visible()


def test_registry_full_offline_flow(admin_console_url: str, page: Page) -> None:
    """Candidate → diff → offline A/B → blocked promote → override → audit →
    rollback — the complete Phase-12 flow through the UI."""
    _open_registry(admin_console_url, page)

    # v1 active, friendly name shown, never raw ids.
    expect(page.get_by_text("Version 1").first).to_be_visible()
    expect(page.get_by_text("Active", exact=True).first).to_be_visible()

    # ── candidate ────────────────────────────────────────────────
    page.get_by_role("button", name="New candidate from active").click()
    template = page.get_by_label("Template")
    expect(template).to_be_visible(timeout=15_000)
    expect(template).not_to_have_value("")
    template.fill(
        "You are the mcp-agent-toolkit assistant. Answer with tools when "
        "they help, keep answers short and cite tool evidence."
    )
    page.get_by_label("Description").fill("Shorter answer style")
    page.get_by_role("button", name="Create candidate").click()
    expect(page.get_by_text("Version 2").first).to_be_visible(timeout=15_000)
    expect(page.get_by_text("Candidate", exact=True).first).to_be_visible()

    # ── diff ─────────────────────────────────────────────────────
    page.get_by_text("Version 2").first.click()
    page.get_by_role("button", name="Compare with active").click()
    expect(page.get_by_label("Changes from version 1 to version 2")).to_be_visible(timeout=15_000)

    # ── offline A/B smoke ────────────────────────────────────────
    page.get_by_role("button", name="Smoke check (offline)").click()
    expect(page.get_by_text("Smoke check — not a quality measurement")).to_be_visible(
        timeout=300_000
    )
    expect(page.get_by_text(re.compile("Gate (passed|failed)"), exact=False).first).to_be_visible()
    expect(page.get_by_role("cell", name="Adversarial resistance")).to_be_visible()

    # ── promote blocked → override ───────────────────────────────
    page.get_by_role("button", name="Promote to active").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_role("button", name="Promote", exact=True).click()
    # Blocked explanation + the override field appears only after a 409.
    expect(dialog.get_by_text(re.compile("promotion blocked|run /ab-test"))).to_be_visible(
        timeout=15_000
    )
    override = dialog.get_by_label(re.compile("Override reason"))
    override.fill("Portfolio demo — smoke mechanics verified, gate bypassed knowingly")
    dialog.get_by_role("button", name="Override and promote").click()
    expect(page.get_by_text("Version 2").first).to_be_visible(timeout=15_000)
    # v2 now carries the Active badge; v1 is rolled back.
    v2_row = page.get_by_role("button", name=re.compile(r"Version 2"))
    expect(v2_row.get_by_text("Active", exact=True)).to_be_visible()

    # ── audit ────────────────────────────────────────────────────
    expect(page.get_by_text("Chain verified")).to_be_visible()
    for label in ("Candidate created", "A/B test run", "Promoted"):
        expect(page.get_by_text(label).first).to_be_visible()
    expect(page.get_by_text("Promoted with override")).to_be_visible()

    # ── rollback ─────────────────────────────────────────────────
    page.get_by_role("button", name=re.compile(r"Version 1")).click()
    page.get_by_role("button", name="Roll back to v1").click()
    reason = page.get_by_label(re.compile("Reason"))
    reason.fill("restoring the original prompt")
    page.get_by_role("button", name="Roll back", exact=True).click()
    v1_row = page.get_by_role("button", name=re.compile(r"Version 1"))
    expect(v1_row.get_by_text("Active", exact=True)).to_be_visible(timeout=15_000)

    # ── sweep on the registry surface ────────────────────────────
    hits = _forbidden_hits(page, "")
    assert hits == [], f"forbidden literals visible: {hits}"
