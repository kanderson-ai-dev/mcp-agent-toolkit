"""Phase-17 §B gate — one Playwright test per `ui_qa_queries.py` entry.

Each test submits the query verbatim against the production build, asserts
the expected card order, badges and humanised copy from the plan's §B
table, then runs the §C forbidden-literal sweep. Q1/Q6/Q9 additionally
sweep the three required viewports for horizontal overflow.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_console import _ask_and_settle, _forbidden_hits, _load_console
from tests.e2e.ui_qa_queries import UI_QUERIES

# §B requires Q1, Q6 and Q9 at all three viewports.
VIEWPORTS = [(1280, 800), (768, 1024), (375, 812)]

UI = {q.qid: q for q in UI_QUERIES}

_BADGES = re.compile(r"^(Success|Error|Running)$")
_RESULT_TOGGLE = re.compile(r"^Result")


@pytest.fixture
def console_errors(page: Page) -> Iterator[list[str]]:
    """§D: collect browser-console errors *and* warnings. Each test
    asserts on the list itself (deliberately-aborted routes produce
    expected network noise and filter it)."""
    bad: list[str] = []
    page.on(
        "console",
        lambda m: bad.append(f"{m.type}: {m.text}") if m.type in {"error", "warning"} else None,
    )
    page.on("pageerror", lambda e: bad.append(f"pageerror: {e}"))
    yield bad


def _expected_network_noise(m: str) -> bool:
    """Aborted-route noise: `net::ERR_…`, failed fetches, stream errors."""
    return any(t in m for t in ("net::", "Failed to fetch", "stream", "ERR_"))


def _runs(page: Page):
    return page.locator("section")


def _latest_run(page: Page):
    return _runs(page).last


def _badges(run) -> list[str]:
    return run.get_by_text(_BADGES).all_text_contents()


def _expand_result(run, index: int = 0) -> None:
    run.get_by_role("button", name=_RESULT_TOGGLE).nth(index).click()


def _composer(page: Page):
    return page.get_by_label("Question for the agent")


def _send(page: Page):
    return page.get_by_label("Send question")


def _settle(page: Page) -> None:
    """Wait until the in-flight run closes (composer re-enables)."""
    expect(_composer(page)).to_be_enabled(timeout=60_000)


def _assert_no_horizontal_overflow(page: Page) -> None:
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"horizontal overflow of {overflow}px"


def _sweep(page: Page, prompt: str) -> None:
    hits = _forbidden_hits(page, prompt)
    assert hits == [], f"forbidden literals visible: {hits}"


def _replay_final(console_url: str, page: Page, question: str) -> dict:
    """Replay `question` through the API and return its `final` SSE
    event payload (browser SSE bodies are consumed by EventSource and
    cannot be re-read; an API-level replay is the honest equivalent)."""
    resp = page.request.post(f"{console_url}/api/chat", data={"question": question})
    assert resp.status == 201, resp.text()
    run_id = resp.json()["runId"]
    stream = page.request.get(f"{console_url}/api/chat/stream?runId={run_id}")
    assert stream.ok, stream.status
    for block in stream.text().split("\n\n"):
        lines = block.splitlines()
        if "event: final" in lines:
            data = next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
            return json.loads(data)
    raise AssertionError("no `final` event in the SSE body")


def _parse_compact_tokens(text: str) -> float:
    """Reverse the footer's `~1.8k tokens` / `~2M tokens` formatting."""
    m = re.search(r"~([\d.]+)([kM]?) tokens", text)
    assert m, text
    return float(m.group(1)) * {"": 1, "k": 1_000, "M": 1_000_000}[m.group(2)]


# ── Q1 · mt-01 — three tools, prompt chip, footer, viewports ──────────


@pytest.mark.e2e
@pytest.mark.parametrize("width,height", VIEWPORTS, ids=["desktop", "tablet", "phone"])
def test_q1_report_pipeline(console_url, page, console_errors, width, height):
    page.set_viewport_size({"width": width, "height": height})
    _load_console(console_url, page)
    q01 = UI["q01"]
    q = q01.prompt
    _ask_and_settle(page, q)
    run = _latest_run(page)

    assert run.locator("h3").all_text_contents() == [
        *(t for t, _ in q01.steps),
        "Final answer",
    ]
    assert _badges(run) == [b for _, b in q01.steps]
    _expand_result(run, 2)  # the write card
    expect(run.get_by_text("File saved:")).to_be_visible()
    expect(run.get_by_text("reports/security-briefing.md").last).to_be_visible()

    # F11: chip and footer must equal the run's `final` SSE payload.
    # SSE bodies can't be re-read in the browser, so the identical prompt
    # is replayed at API level — the stub is deterministic, therefore the
    # telemetry values are identical.
    final = _replay_final(console_url, page, q)
    version = final["promptVersion"]["version"]
    expect(run.get_by_text(f"Prompt v{version}", exact=True)).to_be_visible()
    total = final["usage"]["totalTokens"]
    shown = run.get_by_text(re.compile(r"~[\d.]+[kM]? tokens")).inner_text()
    assert abs(_parse_compact_tokens(shown) - total) / max(total, 1) < 0.05, (
        f"footer {shown!r} vs final payload {total}"
    )
    expect(run.get_by_text(re.compile(r"Time \d"))).to_be_visible()
    expect(run.get_by_text("no tool calls", exact=True)).to_have_count(0)

    _assert_no_horizontal_overflow(page)
    _sweep(page, q)
    assert console_errors == []


# ── Q2 · st-01 — result table with humanised headers ───────────────────


@pytest.mark.e2e
def test_q2_table(console_url, page, console_errors):
    _load_console(console_url, page)
    q = UI["q02"].prompt
    _ask_and_settle(page, q)
    run = _latest_run(page)

    expect(
        run.get_by_role("button", name=re.compile(r"Result: \d+ rows? returned"))
    ).to_be_visible()
    _expand_result(run, 0)

    headers = run.locator("table th").all_inner_texts()
    assert "Title" in headers and "Author" in headers, headers
    for h in headers:
        assert re.fullmatch(r"[A-Z][A-Za-z ]*", h), f"unhumanised header {h!r}"
    assert run.locator("table tbody tr").count() >= 3

    _sweep(page, q)
    assert console_errors == []


# ── Q3 · er-01 — error recovery ────────────────────────────────────────


@pytest.mark.e2e
def test_q3_error_recovery(console_url, page, console_errors):
    _load_console(console_url, page)
    q = UI["q03"].prompt
    _ask_and_settle(page, q)
    run = _latest_run(page)

    assert run.locator("h3").all_text_contents() == [
        "Database query",
        "Database query",
        "Final answer",
    ]
    assert _badges(run) == ["Error", "Success"]

    _expand_result(run, 0)  # failed call → humanised error detail
    expect(run.locator("p.text-danger")).to_be_visible()

    _expand_result(run, 1)  # recovered call → table with humanised dates
    cells = run.locator("table tbody td").all_inner_texts()
    # Locale-agnostic: "Aug 18, 2026" (en) or "18 ago 2026" (es) both pass;
    # a raw ISO timestamp (`2026-08-18T…`) fails.
    human_date = re.compile(r"[A-Za-zÀ-ÿ]{3,}\.? \d{1,2},? \d{4}|\d{1,2} [A-Za-zÀ-ÿ]{3,}\.? \d{4}")
    assert any(human_date.search(c) for c in cells), f"no humanised date cell in {cells}"
    assert not any(re.search(r"\d{4}-\d{2}-\d{2}T", c) for c in cells), cells

    expect(run.get_by_role("heading", name="Final answer")).to_be_visible()
    _sweep(page, q)
    assert console_errors == []


# ── Q4 · st-02 — sources list with secure external links ───────────────


@pytest.mark.e2e
def test_q4_sources(console_url, page, console_errors):
    _load_console(console_url, page)
    q = UI["q04"].prompt
    _ask_and_settle(page, q)
    run = _latest_run(page)

    assert run.locator("h3").all_text_contents() == ["Web search", "Final answer"]
    expect(run.get_by_role("button", name=re.compile(r"Result: \d+ results"))).to_be_visible()
    _expand_result(run, 0)

    expect(run.get_by_text("Source:", exact=False)).to_be_visible()
    links = run.locator("a[target='_blank']")
    assert links.count() >= 1
    for i in range(links.count()):
        rel = links.nth(i).get_attribute("rel") or ""
        assert "noopener" in rel and "noreferrer" in rel, rel

    _sweep(page, q)
    assert console_errors == []


# ── Q5 · st-03 — saved-file path ───────────────────────────────────────


@pytest.mark.e2e
def test_q5_file_saved(console_url, page, console_errors):
    _load_console(console_url, page)
    q = UI["q05"].prompt
    _ask_and_settle(page, q)
    run = _latest_run(page)

    assert run.locator("h3").all_text_contents() == ["File write", "Final answer"]
    _expand_result(run, 0)
    expect(run.get_by_text("File saved:")).to_be_visible()
    expect(run.get_by_text("notes/console-check.md").last).to_be_visible()
    expect(run.get_by_text(re.compile(r"\d+(\.\d+)? (B|KB|MB) written"))).to_be_visible()

    _sweep(page, q)
    assert console_errors == []


# ── Q6 · st-04 — monospace preview wraps at small viewports ────────────


@pytest.mark.e2e
@pytest.mark.parametrize("width,height", VIEWPORTS, ids=["desktop", "tablet", "phone"])
def test_q6_file_preview(console_url, page, console_errors, width, height):
    page.set_viewport_size({"width": width, "height": height})
    _load_console(console_url, page)
    q = UI["q06"].prompt
    _ask_and_settle(page, q)
    run = _latest_run(page)

    assert run.locator("h3").all_text_contents() == ["File read", "Final answer"]
    expect(run.get_by_role("button", name=re.compile(r"Result: \d+"))).to_be_visible()
    _expand_result(run, 0)
    expect(run.locator("pre")).to_have_count(1)
    expect(run.locator("pre")).to_contain_text("mcp-agent-toolkit sandbox")

    _assert_no_horizontal_overflow(page)
    _sweep(page, q)
    assert console_errors == []


# ── Q7 · ad-01 — injection refused, database untouched ─────────────────


@pytest.mark.e2e
def test_q7_injection_refused(console_url, page, console_errors):
    _load_console(console_url, page)
    q = UI["q07"].prompt
    _ask_and_settle(page, q)
    run = _latest_run(page)

    assert _badges(run) == ["Error"]
    expect(run.get_by_text("Blocked by security policy")).to_be_visible()
    assert run.get_by_text("Success", exact=True).count() == 0
    expect(run.locator(".markdown").last).to_contain_text("guardrails")

    # The table must be untouched — a follow-up select still returns rows.
    follow_prompt = UI["q02"].prompt
    _ask_and_settle(page, follow_prompt)
    expect(page.get_by_role("heading", name="Final answer")).to_have_count(2)
    follow = _latest_run(page)
    expect(
        follow.get_by_role("button", name=re.compile(r"Result: \d+ rows? returned"))
    ).to_be_visible()

    _sweep(page, follow_prompt)
    assert console_errors == []


# ── Q8 · ad-02 — path escapes blocked, no host paths visible ───────────


@pytest.mark.e2e
def test_q8_path_escape_blocked(console_url, page, console_errors):
    _load_console(console_url, page)
    q = UI["q08"].prompt
    _ask_and_settle(page, q)
    run = _latest_run(page)

    assert run.locator("h3").all_text_contents() == [
        "File read",
        "File read",
        "Final answer",
    ]
    assert _badges(run) == ["Error", "Error"]
    # Both attempts show the same friendly phrase — never the raw host
    # paths (`C:/Windows/…`, `../../etc/passwd`); those stay in the
    # collapsed raw JSON only.
    assert run.get_by_text("a path outside the workspace", exact=True).count() == 2
    assert run.get_by_text("Blocked by security policy").count() == 2
    assert run.locator("pre").count() == 0  # no file content anywhere
    expect(run.locator(".markdown").last).to_contain_text("guardrails")

    _sweep(page, q)
    assert console_errors == []


# ── Q9 · am-01 — zero-tool answer ──────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.parametrize("width,height", VIEWPORTS, ids=["desktop", "tablet", "phone"])
def test_q9_no_tools(console_url, page, console_errors, width, height):
    page.set_viewport_size({"width": width, "height": height})
    _load_console(console_url, page)
    q = UI["q09"].prompt
    _ask_and_settle(page, q)
    run = _latest_run(page)

    assert run.locator("h3").all_text_contents() == ["Final answer"]
    expect(run.get_by_text("Answered without tools", exact=True)).to_be_visible()
    expect(run.get_by_text("no tool calls", exact=True)).to_be_visible()

    _assert_no_horizontal_overflow(page)
    _sweep(page, q)
    assert console_errors == []


# ── §D resilience checks ───────────────────────────────────────────────


@pytest.mark.e2e
def test_sse_interruption_recovers(console_url, page, console_errors):
    """F3: a dropped SSE stream yields the recoverable error state, a
    "Try again" affordance and a re-enabled composer — retrying runs
    the same question to completion."""
    _load_console(console_url, page)
    page.route("**/api/chat/stream*", lambda route: route.abort())
    q = UI["q02"].prompt
    _ask_and_settle(page, q)
    run = _latest_run(page)

    expect(run.get_by_text("The request could not be completed")).to_be_visible()
    expect(_composer(page)).to_be_enabled()
    retry = run.get_by_role("button", name="Try again")
    expect(retry).to_be_visible()

    page.unroute("**/api/chat/stream*")
    retry.click()
    expect(_composer(page)).to_be_disabled()  # retry started a new run
    _settle(page)
    expect(page.get_by_role("heading", name="Final answer")).to_have_count(1)
    follow = _latest_run(page)
    expect(
        follow.get_by_role("button", name=re.compile(r"Result: \d+ rows? returned"))
    ).to_be_visible()
    _sweep(page, q)
    # The aborted attempt logs network noise in the console — expected;
    # only *unexpected* errors fail the check.
    assert all(_expected_network_noise(m) for m in console_errors), console_errors


@pytest.mark.e2e
def test_backend_down_banner(console_url, page, console_errors):
    """F4: with the API unreachable the banner state shows and
    "Try again" reloads into a working console once it's back."""
    page.route("**/api/health*", lambda route: route.abort())
    page.route("**/api/tools*", lambda route: route.abort())
    page.goto(console_url, wait_until="domcontentloaded")
    expect(page.get_by_text("Can't reach the agent.")).to_be_visible()
    retry = page.get_by_role("button", name="Try again")
    expect(retry).to_be_visible()
    expect(_composer(page)).to_be_disabled()

    page.unroute("**/api/health*")
    page.unroute("**/api/tools*")
    retry.click()
    expect(_composer(page)).to_be_enabled(timeout=30_000)
    assert all(_expected_network_noise(m) for m in console_errors), console_errors


@pytest.mark.e2e
def test_scroll_follow_stops_when_user_scrolls_up(console_url, page, console_errors):
    """F8: the stream auto-follows while at the bottom; once the reader
    scrolls up, incoming cards must not yank the view back down."""
    _load_console(console_url, page)
    _ask_and_settle(page, UI["q01"].prompt)  # tall first run → scrollable page

    page.route(
        re.compile(r"/api/chat/stream"),
        lambda route: (time.sleep(1.2), route.continue_()),
    )
    _composer(page).fill(UI["q02"].prompt)
    _send(page).click()
    # New submit re-engages following; let that smooth scroll settle, then
    # the reader scrolls up before the held stream emits its tool cards.
    expect(_composer(page)).to_be_disabled()
    page.wait_for_timeout(400)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(900)  # stream released; events land while at top
    page.unroute(re.compile(r"/api/chat/stream"))
    _settle(page)
    assert page.evaluate("window.scrollY") < 60, "auto-scroll yanked a scrolled-up view"
    _sweep(page, UI["q02"].prompt)
    assert console_errors == []


@pytest.mark.e2e
def test_reload_drops_stale_runs(console_url, page, console_errors):
    """F9: a page reload must not resurrect stale runs — the console
    is a live view, not a persisted transcript."""
    _load_console(console_url, page)
    q = UI["q02"].prompt
    _ask_and_settle(page, q)
    assert _runs(page).count() == 1
    page.reload(wait_until="domcontentloaded")
    expect(_composer(page)).to_be_enabled(timeout=30_000)
    assert _runs(page).count() == 0
    expect(page.get_by_text("Try one of these questions")).to_be_visible()
    assert console_errors == []


# ── Q10 · mt-02 — history stacks, validation, input guards ─────────────


@pytest.mark.e2e
def test_q10_repeat_runs_and_validation(console_url, page, console_errors):
    _load_console(console_url, page)
    q = UI["q10"].prompt

    _ask_and_settle(page, q)
    _ask_and_settle(page, q)
    expect(page.get_by_role("heading", name="Final answer")).to_have_count(2)
    assert _runs(page).count() == 2

    composer = _composer(page)
    send = _send(page)

    # Oversized input: friendly message, send disabled, no run.
    composer.fill("x" * 2001)
    expect(page.get_by_text(re.compile(r"Keep it under 2.000 characters\."))).to_be_visible()
    expect(send).to_be_disabled()
    assert _runs(page).count() == 2

    # Too-short input: friendly message, send disabled, no run.
    composer.fill("hi")
    expect(page.get_by_text("Questions need at least 3 characters.")).to_be_visible()
    expect(send).to_be_disabled()
    assert _runs(page).count() == 2

    # Double-submit guard: while a run streams, composer and send disable.
    composer.fill(q)
    send.click()
    expect(composer).to_be_disabled()
    _settle(page)
    expect(page.get_by_role("heading", name="Final answer")).to_have_count(3)

    # Keyboard: Shift+Enter inserts a newline, Enter submits.
    composer.fill("line one")
    composer.press("Shift+Enter")
    composer.press("l")
    assert "\n" in (composer.input_value() or "")
    composer.fill(q)
    composer.press("Enter")
    expect(page.get_by_role("heading", name="Final answer")).to_have_count(4)
    _settle(page)

    _sweep(page, q)
    assert console_errors == []
