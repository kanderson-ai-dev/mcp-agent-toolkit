"""Console e2e — the dataset driven through the real UI.

Every ``uiExample`` question (plus the remaining dataset rows, for full
coverage) is submitted to the production build served by the real FastAPI
backend (StubLLM + ``SEARCH_PROVIDER=none``, real MCP stdio transport).
Assertions: the question appears verbatim, tool cards render friendly
labels in the scripted order with matching status badges, the final
answer renders as Markdown, and the §C forbidden-literal + snake/camel
regex sweep passes on all visible text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def _load_questions() -> list[dict[str, Any]]:
    raw = json.loads(
        (Path(__file__).resolve().parents[2] / "evaluation" / "dataset.json").read_text(
            encoding="utf-8"
        )
    )
    return list(raw["questions"])


QUESTIONS = _load_questions()

# Expected card sequence per question — mirrors the deterministic StubLLM
# plan (tests/unit/test_stub_llm.py documents the same branches).
EXPECTED_STEPS: dict[str, list[tuple[str, str]]] = {
    "mt-01": [("Database query", "Success"), ("Web search", "Success"), ("File write", "Success")],
    "mt-02": [("Database query", "Success"), ("Web search", "Success")],
    "mt-03": [("File read", "Success"), ("Web search", "Success")],
    "st-01": [("Database query", "Success")],
    "st-02": [("Web search", "Success")],
    "st-03": [("File write", "Success")],
    "st-04": [("File read", "Success")],
    "er-01": [("Database query", "Error"), ("Database query", "Success")],
    "er-02": [("Database query", "Error"), ("Database query", "Success")],
    "ad-01": [("Database query", "Error")],
    "ad-02": [("File read", "Error"), ("File read", "Error")],
    "ad-03": [("Web search", "Success")],
    "ad-04": [("Web search", "Success")],
    "ad-05": [("File write", "Error")],
    "ad-06": [("Database query", "Error")],
    "ad-07": [],
    "ad-08": [],
    "ad-09": [("File read", "Error"), ("File read", "Error")],
    "ad-10": [("Database query", "Error")],
    "ad-11": [("File write", "Error")],
    "ad-12": [("Database query", "Success")] * 70,
    "ad-13": [("File write", "Error")],
    "am-01": [],
    "am-02": [],
}

# §C: internal field names that must never appear as visible UI text.
FORBIDDEN_LITERALS = [
    "tool_call",
    "tool_result",
    "llm_message",
    "request_id",
    "requestId",
    "session_id",
    "durationMs",
    "terminatedBy",
    "iteration_limit",
    "budget_limit",
    "max_results",
    "row_count",
    "bytes_written",
    "isError",
    "toolCalls",
    "costUsd",
    "promptVersion",
    "prompt_version",
    "override_reason",
    "rolled_back",
    "agent_system",
    "judge_rubric",
    "candidate_created",
    "promoted_override",
    "ab_test",
    "taskSuccess",
    "toolUsageAppropriatePct",
    "adversarialResisted",
    "gatePassed",
    "promotionAllowed",
    "baselineVersion",
    "candidateVersion",
    "promotedVersion",
    "modelHint",
    "offline-smoke",
    "adminEnabled",
    "firstTamperedLine",
    "ab_id",
    "runId",
    "sha256",
    "web_search",
    "db_query",
    "read_file",
    "write_file",
    "SQLITE_",
    "undefined",
    "[object Object]",
    '{"status"',
]

_CAMEL_OR_SNAKE = re.compile(r"\b[a-z]+_[a-z_]+\b|\b[a-z]+[A-Z][A-Za-z]+\b")

# Visible-text sources allowed to contain internal-looking tokens:
# the user's own question and inline file paths/SQL (rendered inside
# <code>/<pre>, which the sweep strips before scanning).
#
# The sweep walks text nodes individually rather than joining innerText:
# innerText concatenates adjacent inline elements without separators, which
# fabricates camelCase-looking tokens across element seams ("…tools" +
# "Demo" → "toolsDemo"). An internal identifier can only exist inside a
# single text node, so per-node matching is both stricter and exact.
_VISIBLE_NODES_JS = """() => {
  const walker = document.createTreeWalker(
    document.body,
    NodeFilter.SHOW_TEXT,
    (node) => {
      const el = node.parentElement;
      if (!el) return NodeFilter.FILTER_REJECT;
      if (el.closest('code, pre, [role="status"], script, style')) {
        return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    }
  );
  const texts = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    texts.push(n.nodeValue ?? '');
  }
  return texts;
}"""


def _visible_text_nodes(page: Page) -> list[str]:
    return list(page.evaluate(_VISIBLE_NODES_JS))


def _forbidden_hits(page: Page, question_prompt: str) -> list[str]:
    nodes = _visible_text_nodes(page)
    text = "\n".join(nodes)
    hits = [lit for lit in FORBIDDEN_LITERALS if lit in text]
    for node in nodes:
        hits += [
            m.group(0) for m in _CAMEL_OR_SNAKE.finditer(node) if m.group(0) not in question_prompt
        ]
    return sorted(set(hits))


def _ask_and_settle(page: Page, prompt: str) -> None:
    composer = page.get_by_label("Question for the agent")
    composer.fill(prompt)
    page.get_by_label("Send question").click()
    # A finished run always ends with either the final-answer card or the
    # error state; the composer re-enables once the stream closes.
    final_or_error = page.get_by_role("heading", name="Final answer").or_(
        page.get_by_text("The request could not be completed")
    )
    expect(final_or_error.first).to_be_visible(timeout=60_000)
    expect(composer).to_be_enabled(timeout=30_000)


def _load_console(console_url: str, page: Page) -> None:
    # `domcontentloaded` + an explicit readiness wait is deterministic;
    # `networkidle` is not — SSE heartbeats and async assets can delay it.
    page.goto(console_url, wait_until="domcontentloaded")
    expect(page.get_by_label("Question for the agent")).to_be_enabled(timeout=30_000)


@pytest.mark.parametrize("question", QUESTIONS, ids=[q["id"] for q in QUESTIONS])
def test_dataset_question(console_url: str, page: Page, question: dict[str, Any]) -> None:
    _load_console(console_url, page)
    _ask_and_settle(page, question["prompt"])

    run = page.locator("section").last

    # The question is echoed verbatim in the run history.
    expect(run.get_by_text(question["prompt"], exact=True)).to_be_visible()

    # Tool cards render friendly names in the scripted order with the
    # matching status badges — never raw tool ids or flags.
    expected = EXPECTED_STEPS[question["id"]]
    card_titles = run.locator("h3").all_text_contents()
    assert card_titles[: len(expected)] == [s[0] for s in expected]
    badges = [
        t for t in run.get_by_text(re.compile(r"^(Success|Error|Running)$")).all_text_contents()
    ]
    assert badges == [s[1] for s in expected]

    # Humanized details are present where tool calls happened.
    if expected:
        expect(run.get_by_text(re.compile(r"Step \d+")).first).to_be_visible()
        expect(run.get_by_text("Trace ID:").first).to_be_visible()

    # The final answer renders as markdown inside its card.
    expect(run.get_by_role("heading", name="Final answer")).to_be_visible()
    expect(run.locator(".markdown").last).to_be_visible()

    # §C sweep: no internal field names or snake/camel identifiers visible.
    hits = _forbidden_hits(page, question["prompt"])
    assert hits == [], f"forbidden literals visible: {hits}"


@pytest.mark.parametrize("qid", ["ad-01", "ad-02"])
def test_adversarial_shows_blocked_state(console_url: str, page: Page, qid: str) -> None:
    q = next(q for q in QUESTIONS if q["id"] == qid)
    _load_console(console_url, page)
    _ask_and_settle(page, q["prompt"])

    run = page.locator("section").last
    expect(run.get_by_text("Blocked by security policy").first).to_be_visible()
    # Zero successes: every attempted call was rejected.
    assert run.get_by_text("Success", exact=True).count() == 0
    # The refusal is the assistant's voice — no injected content.
    expect(run.get_by_role("heading", name="Final answer")).to_be_visible()


def test_empty_state_example_chips_fill_composer(console_url: str, page: Page) -> None:
    _load_console(console_url, page)
    chip = page.get_by_role("button", name=re.compile("security reports"))
    expect(chip.first).to_be_visible()
    chip.first.click()
    composer = page.get_by_label("Question for the agent")
    expect(composer).to_have_value(re.compile(r"security reports"))
    # Chips fill the composer — they do not auto-submit.
    expect(page.get_by_role("heading", name="Final answer")).to_have_count(0)
