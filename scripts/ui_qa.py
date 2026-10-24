"""UI-QA capture — screenshots + automated visual checks of the console.

Boots the production console (`python -m mcp_toolkit.web`, offline stack),
runs each `tests/e2e/ui_qa_queries.py` query at its required viewports and
captures evidence into ``docs/ui-qa/``:

- ``q<NN>-<viewport>.png`` per query/viewport (full page, dark theme)
- ``state-{empty,empty-light,streaming,result,error}.png``
- ``results.json`` — per-shot automated checks: horizontal overflow,
  WCAG contrast of visible text (≥4.5 body / ≥3.0 large), mobile tap
  targets ≥44 px and CLS (<0.02, ``PerformanceObserver``)

Exit code 1 on any check failure — the checklist cites this file's
verdicts as evidence. Run: ``uv run python scripts/ui_qa.py``.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.e2e.conftest import _boot_server  # noqa: E402
from tests.e2e.ui_qa_queries import UI_QUERIES  # noqa: E402

OUT = ROOT / "docs" / "ui-qa"
VIEWPORT_NAMES = {(1280, 800): "desktop", (768, 1024): "tablet", (375, 812): "phone"}

# Accumulate layout-shift score from page load; input-caused shifts are
# excluded per the CLS spec.
CLS_INIT = """
window.__cls = 0;
new PerformanceObserver((list) => {
  for (const e of list.getEntries()) {
    if (!e.hadRecentInput) window.__cls += e.value;
  }
}).observe({type: 'layout-shift', buffered: true});
"""

# WCAG 2.x contrast of every visible text element: computed foreground
# composited over the effective ancestor background chain.
CONTRAST_JS = """() => {
  const parse = (c) => {
    const m = /rgba?\\(([^)]+)\\)/.exec(c);
    if (!m) return null;
    const p = m[1].split(',').map(Number);
    return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1};
  };
  const over = (top, base) => ({
    r: top.a * top.r + (1 - top.a) * base.r,
    g: top.a * top.g + (1 - top.a) * base.g,
    b: top.a * top.b + (1 - top.a) * base.b,
  });
  const effBg = (el) => {
    const stack = [];
    for (let n = el.parentElement; n; n = n.parentElement) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0) stack.push(c);
      if (c && c.a >= 1) break;
    }
    let bg = {r: 255, g: 255, b: 255};
    for (let i = stack.length - 1; i >= 0; i--) bg = over(stack[i], bg);
    return bg;
  };
  const lum = (c) => {
    const f = (v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const out = [];
  const seen = new Set();
  const els = document.querySelectorAll(
    'h1,h2,h3,h4,p,td,th,span,a,button,label,li,code,pre'
  );
  for (const el of els) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0)
      continue;
    const txt = (el.innerText || '').trim();
    if (!txt) continue;
    const fg = parse(cs.color);
    if (!fg) continue;
    const bg = effBg(el);
    let ratio;
    if (fg.a === 0 && cs.backgroundImage.includes('gradient')) {
      // Gradient-clipped text (`background-clip: text`): the painted
      // colour is the gradient — measure the WORST stop against the
      // effective background.
      const stops = [...cs.backgroundImage.matchAll(/rgba?\\([^)]+\\)/g)]
        .map((m) => parse(m[0]))
        .filter(Boolean);
      ratio = !stops.length ? 1 : Math.min(
        ...stops.map((s) => {
          const l1 = lum(over(s, bg)), l2 = lum(bg);
          return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
        })
      );
    } else {
      const fgEff = over(fg, bg);
      const l1 = lum(fgEff), l2 = lum(bg);
      ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    }
    const fs = parseFloat(cs.fontSize);
    const wt = parseInt(cs.fontWeight, 10) || 400;
    const large = fs >= 24 || (fs >= 18.66 && wt >= 600);
    const bgk = `${Math.round(bg.r)},${Math.round(bg.g)},${Math.round(bg.b)}`;
    const key = `${fg.r},${fg.g},${fg.b},${fg.a}|${bgk}|${fs}|${wt}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({sample: txt.slice(0, 48), ratio: +ratio.toFixed(2), fs, wt, large});
  }
  return out;
}"""

# Interactive elements must be ≥44 px squares on the phone viewport.
TAP_JS = """() => {
  const out = [];
  const els = document.querySelectorAll('button, a, [role="button"], input, textarea');
  for (const el of els) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    // 0.5 px tolerance: getBoundingClientRect is sub-pixel (a 44 px
    // min-height can report 43.5–43.9 due to line-box rounding).
    if (r.width < 43.5 || r.height < 43.5) {
      const label = (el.getAttribute('aria-label') || el.innerText || el.tagName)
        .trim().slice(0, 48);
      out.push({label, w: +r.width.toFixed(1), h: +r.height.toFixed(1)});
    }
  }
  return out;
}"""

ASK = re.compile(r"^(Success|Error|Running)$")


def _submit(page, prompt: str) -> None:
    page.get_by_label("Question for the agent").fill(prompt)
    page.get_by_label("Send question").click()


def _settle(page) -> None:
    final_or_error = page.get_by_role("heading", name="Final answer").or_(
        page.get_by_text("The request could not be completed")
    )
    expect(final_or_error.first).to_be_visible(timeout=60_000)
    expect(page.get_by_label("Question for the agent")).to_be_enabled(timeout=30_000)


def _checks(page, mobile: bool) -> dict[str, Any]:
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    cls = page.evaluate("window.__cls ?? 0")
    contrast = page.evaluate(CONTRAST_JS)
    fails = [c for c in contrast if c["ratio"] < (3.0 if c["large"] else 4.5)]
    report: dict[str, Any] = {
        "horizontalOverflowPx": overflow,
        "cls": round(cls, 4),
        "contrastFailures": fails,
    }
    if mobile:
        report["tapTargetsBelow44px"] = page.evaluate(TAP_JS)
    ok = overflow <= 1 and cls < 0.02 and not fails and not report.get("tapTargetsBelow44px")
    report["ok"] = ok
    return report


LIVE_QIDS = {"q01", "q04", "q08"}


def _run_live() -> int:
    """Live pass: Q1/Q4/Q8 against real providers (requires OPENAI_API_KEY
    in `.env`; Tavily when SEARCH_API_KEY is set, else DuckDuckGo).
    Screenshots land as ``q<NN>-live.png``; verdicts go to
    ``results-live.json``. A real model's card order can differ from the
    scripted stub plan — the gate is: run completes, no forbidden
    literals, ≥1 successful tool card, sources for Q4."""
    from tests.e2e.test_console import _forbidden_hits

    gen = _boot_server(
        "ui-qa-live.log",
        {
            "LLM_PROVIDER": "auto",
            "SEARCH_PROVIDER": "auto",
            "CHAT_MODEL_NAME": "gpt-4o-mini",
            "RATE_LIMIT_PER_MINUTE": "60",
        },
    )
    url = next(gen)
    results: dict[str, Any] = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for q in UI_QUERIES:
                if q.qid not in LIVE_QIDS:
                    continue
                page = browser.new_page(
                    viewport={"width": 1280, "height": 800}, color_scheme="dark"
                )
                page.add_init_script(CLS_INIT)
                page.goto(url, wait_until="domcontentloaded")
                expect(page.get_by_label("Question for the agent")).to_be_enabled(timeout=30_000)
                _submit(page, q.prompt)
                _settle(page)
                page.locator("div.sticky").evaluate("e => e.style.visibility = 'hidden'")
                page.locator("section").last.screenshot(
                    path=str(OUT / f"{q.qid}-live.png"), animations="disabled"
                )
                run = page.locator("section").last
                badges = run.get_by_text(ASK).all_text_contents()
                hits = _forbidden_hits(page, q.prompt)
                entry = _checks(page, mobile=False)
                entry["badges"] = badges
                entry["forbidden"] = hits
                entry["ok"] = entry["ok"] and not hits
                if q.qid == "q08":
                    # Adversarial: a refusal without tool calls is ideal;
                    # attempts blocked by the sandbox are acceptable too.
                    # Only a successful escape fails the check.
                    entry["ok"] = entry["ok"] and "Success" not in badges
                else:
                    entry["ok"] = entry["ok"] and "Success" in badges
                if q.qid == "q04":
                    entry["ok"] = entry["ok"] and run.locator("a[target='_blank']").count() >= 1
                results[f"{q.qid}-live"] = entry
                print(f"{q.qid}-live: {'ok' if entry['ok'] else 'FAIL'} {entry}")
                page.close()
            browser.close()
    finally:
        gen.close()
    (OUT / "results-live.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    failed = [k for k, v in results.items() if not v.get("ok", True)]
    print(f"\nlive: {len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # windows cp1252
    if "--live" in sys.argv:
        return _run_live()
    OUT.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}
    gen = _boot_server("ui-qa-server.log", {})
    url = next(gen)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()

            # ── Per-query captures at required viewports ────────────
            for q in UI_QUERIES:
                for w, h in q.viewports:
                    vp = VIEWPORT_NAMES[(w, h)]
                    page = browser.new_page(viewport={"width": w, "height": h}, color_scheme="dark")
                    requests: list[str] = []

                    def on_request(r, bucket: list[str] = requests) -> None:
                        if "/api/chat" in r.url:
                            bucket.append(r.url)

                    page.on("request", on_request)
                    page.add_init_script(CLS_INIT)
                    page.goto(url, wait_until="domcontentloaded")
                    expect(page.get_by_label("Question for the agent")).to_be_enabled(
                        timeout=30_000
                    )
                    _submit(page, q.prompt)
                    _settle(page)
                    # Element shot of the run section — a full-page stitch
                    # smears the sticky composer's translucent backdrop over
                    # the lower half of the image (capture artifact, not a
                    # UI bug). The composer itself is viewport chrome, not
                    # part of the run — hidden for the shot so the whole
                    # section is legible. `animations="disabled"` settles
                    # the fade-up so evidence shows the final frame.
                    page.locator("div.sticky").evaluate("e => e.style.visibility = 'hidden'")
                    page.locator("section").last.screenshot(
                        path=str(OUT / f"{q.qid}-{vp}.png"), animations="disabled"
                    )
                    entry = _checks(page, mobile=(w <= 400))
                    entry["apiChatRequests"] = sum(1 for u in requests if u.endswith("/api/chat"))
                    entry["sseStreams"] = sum(1 for u in requests if "stream" in u)
                    results[f"{q.qid}-{vp}"] = entry
                    print(f"{q.qid}-{vp}: {'ok' if entry['ok'] else 'FAIL'} {entry}")
                    page.close()

            # ── Designed states ─────────────────────────────────────
            page = browser.new_page(viewport={"width": 1280, "height": 800}, color_scheme="dark")
            page.add_init_script(CLS_INIT)
            page.goto(url, wait_until="domcontentloaded")
            expect(page.get_by_label("Question for the agent")).to_be_enabled(timeout=30_000)
            page.screenshot(
                path=str(OUT / "state-empty.png"), full_page=True, animations="disabled"
            )
            results["state-empty"] = _checks(page, mobile=False)

            page.get_by_label("Switch to light theme").click()
            page.wait_for_timeout(100)
            page.screenshot(
                path=str(OUT / "state-empty-light.png"), full_page=True, animations="disabled"
            )
            results["state-empty-light"] = _checks(page, mobile=False)
            page.get_by_label("Switch to dark theme").click()
            page.wait_for_timeout(100)

            # Streaming: the stub answers tool calls sub-millisecond, so
            # a "Running" badge frame is not capturable deterministically
            # (badge styling is pinned by ToolStepCard.test.tsx instead).
            # Holding the chat POST open yields a stable in-flight frame:
            # question posted, composer disabled, "working" indicator on.
            page.route(
                re.compile(r"/api/chat$"),
                lambda route: (time.sleep(1.5), route.continue_()),
            )
            _submit(page, UI_QUERIES[0].prompt)
            working = page.get_by_text("The agent is working", exact=False)
            expect(working).to_be_visible()
            expect(page.get_by_label("Question for the agent")).to_be_disabled()
            page.screenshot(path=str(OUT / "state-streaming.png"), full_page=True)
            page.unroute(re.compile(r"/api/chat$"))
            _settle(page)

            # Result state with expanded panel — element shot with the
            # sticky composer hidden (viewport chrome, not part of the run).
            run = page.locator("section").last
            run.get_by_role("button", name=re.compile(r"^Result")).first.click()
            page.locator("div.sticky").evaluate("e => e.style.visibility = 'hidden'")
            run.screenshot(path=str(OUT / "state-result.png"), animations="disabled")
            results["state-result"] = _checks(page, mobile=False)
            page.locator("div.sticky").evaluate("e => e.style.visibility = ''")

            # Error state: the guardrail-blocked run.
            _submit(page, UI_QUERIES[6].prompt)  # q07 · ad-01
            _settle(page)
            page.locator("div.sticky").evaluate("e => e.style.visibility = 'hidden'")
            page.locator("section").last.screenshot(
                path=str(OUT / "state-error.png"), animations="disabled"
            )
            results["state-error"] = _checks(page, mobile=False)
            page.close()
            browser.close()
    finally:
        gen.close()

    results_path = OUT / "results.json"
    results_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    failed = [k for k, v in results.items() if not v.get("ok", True)]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed → {results_path}")
    if failed:
        print("FAILED:", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
