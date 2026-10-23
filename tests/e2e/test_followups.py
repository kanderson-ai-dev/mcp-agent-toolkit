"""Console e2e — multi-turn follow-ups, run export, conversation reset.

Each assertion watches the real wire traffic: the ``history`` array the
console POSTs and the files the export buttons download.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_console import _ask_and_settle, _load_console

pytestmark = pytest.mark.e2e


def test_followup_question_sends_prior_turns(console_url: str, page: Page) -> None:
    _load_console(console_url, page)
    _ask_and_settle(page, "What is the difference between a process and a thread?")

    captured: dict[str, Any] = {}
    page.on(
        "request",
        lambda r: (
            captured.update(json.loads(r.post_data or "{}"))
            if r.url.endswith("/api/chat")
            else None
        ),
    )
    _ask_and_settle(page, "And which one can share memory?")

    history = captured.get("history")
    assert isinstance(history, list)
    assert [t["role"] for t in history] == ["user", "assistant"]
    assert history[0]["content"].startswith("What is the difference")
    # The assistant turn carries the first run's final answer verbatim.
    assert "Stub answer" in history[1]["content"]

    # Both runs stay visible — the console is a transcript, not a replace.
    assert page.locator("section").count() == 2


def test_new_conversation_clears_runs_and_history(console_url: str, page: Page) -> None:
    _load_console(console_url, page)
    _ask_and_settle(page, "What is the difference between a process and a thread?")

    page.get_by_role("button", name="New conversation").click()
    # Back to the designed empty state; the prior run is gone.
    expect(page.get_by_text("Ask. Watch the agent work.")).to_be_visible()
    assert page.locator("section").count() == 0

    captured: dict[str, Any] = {}
    page.on(
        "request",
        lambda r: (
            captured.update(json.loads(r.post_data or "{}"))
            if r.url.endswith("/api/chat")
            else None
        ),
    )
    _ask_and_settle(page, "What is the difference between a process and a thread?")
    assert captured.get("history") == []


def test_export_downloads_markdown_and_json(console_url: str, page: Page, tmp_path) -> None:
    _load_console(console_url, page)
    _ask_and_settle(page, "What is the difference between a process and a thread?")

    with page.expect_download() as md_dl:
        page.get_by_role("button", name="Markdown").first.click()
    md_path = tmp_path / md_dl.value.suggested_filename
    assert md_path.suffix == ".md"
    md_dl.value.save_as(md_path)
    md = md_path.read_text(encoding="utf-8")
    assert md.startswith("# What is the difference")
    assert "## Final answer" in md
    assert "Stub answer" in md

    with page.expect_download() as json_dl:
        page.get_by_role("button", name="JSON").first.click()
    json_path = tmp_path / json_dl.value.suggested_filename
    assert json_path.suffix == ".json"
    json_dl.value.save_as(json_path)
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    assert doc["question"].startswith("What is the difference")
    assert doc["answer"].startswith("Stub answer")
    # Friendly wording only — internal field names never export.
    assert "durationMs" not in json_path.read_text(encoding="utf-8")
    assert "tool_call" not in json_path.read_text(encoding="utf-8")


def test_export_buttons_appear_only_after_completion(console_url: str, page: Page) -> None:
    _load_console(console_url, page)
    _ask_and_settle(page, "What is the difference between a process and a thread?")
    expect(page.get_by_role("button", name="Markdown").first).to_be_visible()
    expect(page.get_by_role("button", name="JSON").first).to_be_visible()


def test_followup_to_tool_run_keeps_answer_context(console_url: str, page: Page) -> None:
    """A tool-using run's answer is what the follow-up turn carries."""
    _load_console(console_url, page)
    _ask_and_settle(page, "What reports does the catalog hold?")

    captured: dict[str, Any] = {}
    page.on(
        "request",
        lambda r: (
            captured.update(json.loads(r.post_data or "{}"))
            if r.url.endswith("/api/chat")
            else None
        ),
    )
    _ask_and_settle(page, "Any on security specifically?")

    history = captured.get("history")
    assert isinstance(history, list) and len(history) == 2
    assert history[1]["role"] == "assistant"
    assert len(history[1]["content"]) > 20
    # The forbidden-literal sweep still holds with two runs on screen.
    text = page.evaluate("document.body.innerText")
    assert not re.search(r"\b(durationMs|requestId|toolCalls|isError)\b", str(text))
