# Functional QA checklist

Ten-query behavioural gate for the web console, run against the production
build with the deterministic backend (`LLM_PROVIDER=stub`,
`SEARCH_PROVIDER=none`). One Playwright test per query lives in
[`tests/e2e/test_ui_qa.py`](../../tests/e2e/test_ui_qa.py); the queries and
their expected tool steps are defined once in
[`tests/e2e/ui_qa_queries.py`](../../tests/e2e/ui_qa_queries.py) and guarded
against `evaluation/dataset.json` drift. §C invariants (forbidden-literal
sweep, snake/camel regex scan, `aria-live` announcements, zero console errors,
exactly one `POST /api/chat` + one SSE stream) run after **every** query.

## §B — the ten queries

| # | Dataset | Expected cards | Result | Evidence |
|---|---|---|---|---|
| Q1 | mt-01 | Database query → Web search → File write, all Success | ✓ | `test_q1_report_pipeline` (3 viewports), `q01-*.png`; footer verified against the replayed `final` payload |
| Q2 | st-01 | Database query Success | ✓ | `test_q2_table`, `q02-desktop.png` — humanised headers ("Title", "Author", "Published At") |
| Q3 | er-01 | Database query Error → Database query Success | ✓ | `test_q3_error_recovery`, `q03-desktop.png` — human error copy, localised dates |
| Q4 | st-02 | Web search Success | ✓ | `test_q4_sources`, `q04-desktop.png` — "Source:" labels, `rel="noopener noreferrer"` |
| Q5 | st-03 | File write Success | ✓ | `test_q5_file_saved`, `q05-desktop.png` — "File saved: notes/console-check.md · N B written" |
| Q6 | st-04 | File read Success | ✓ | `test_q6_file_preview` (3 viewports), `q06-*.png` — monospace preview contained at 375 px |
| Q7 | ad-01 | Database query Error "Blocked by security policy" | ✓ | `test_q7_injection_refused`, `q07-desktop.png` — zero Success badges; follow-up Q2 still returns rows |
| Q8 | ad-02 | File read Error × 2 | ✓ | `test_q8_path_escape_blocked`, `q08-desktop.png` — both blocked, same friendly label, escape paths shown as "a path outside the workspace" |
| Q9 | am-01 | none | ✓ | `test_q9_no_tools` (3 viewports), `q09-*.png` — "Answered without tools", markdown rendered |
| Q10 | mt-02 | Database query → Web search | ✓ | `test_q10_repeat_runs_and_validation`, `q10-desktop.png` — second run stacks; 2 001-char and 2-char inputs rejected with human messages; double-submit blocked |

## §D — functional checks

| ID | Check | Status | Evidence |
|---|---|---|---|
| F1 | Q1–Q10 expected card sequence and badges (order-sensitive) | ✓ | `test_ui_qa.py` — `h3` titles and badge order asserted per query |
| F2 | Final answer always last; distinct endings | ✓ | Every test asserts "Final answer" last; `iteration_limit` → "step limit reached", `budget_limit` → "budget reached", error → "The request could not be completed" (unit + e2e) |
| F3 | SSE interruption → recoverable error + "Try again"; composer re-enabled | ✓ | `test_sse_interruption_recovers` — aborted stream → error card → retry resubmits the same question to completion |
| F4 | Backend down at load → "Can't reach the agent." banner | ✓ | `test_backend_down_banner` — banner + disabled composer; "Try again" reloads into a working console |
| F5 | Example chips fill the composer, don't auto-submit, keyboard-activatable | ✓ | `test_console.py::test_empty_state_example_chips_fill_composer`; chips are native `<button>`s |
| F6 | Technical details collapsed by default, pretty JSON, copy works | ✓ | `TechnicalDetails.test.tsx`; e2e expands "View raw JSON" per query |
| F7 | Markdown safety: `<script>`, `javascript:` links, `<img onerror>` inert | ✓ | `FinalAnswerCard.test.tsx` hostile-markdown test — no script/img nodes, no `javascript:` href executed |
| F8 | Long run keeps scroll anchored; manual scroll-up stops auto-scroll | ✓ | `test_scroll_follow_stops_when_user_scrolls_up` — stream held open, reader scrolls up, incoming cards do not yank the view |
| F9 | Reload does not resurrect a stale run | ✓ | `test_reload_drops_stale_runs` — reload → zero runs, empty state |
| F10 | Validation: too short, too long, whitespace-only, double-submit | ✓ | `test_q10_repeat_runs_and_validation` — 2-char and 2 001-char inputs show human messages; composer stays enabled; no run created; submit blocked while streaming |
| F11 | Prompt chip and cost footer equal the run's `final` event | ✓ | `test_q1_report_pipeline` replays the identical question at API level and compares `promptVersion`/`totalTokens` to the rendered chip and footer |
| F12 | **[REG]** candidate → A/B → blocked promote → override → rollback; admin-disabled hides controls | ✓ | `tests/e2e/test_registry.py` + `tests/integration` registry API matrix |

## §C — cross-query invariants

| Check | Status | Where |
|---|---|---|
| Forbidden literals invisible (`tool_call`, `durationMs`, `isError`, `costUsd`, raw tool names, `undefined`, `null`, `NaN`, `{"status"` …) | ✓ | `_sweep` → `_forbidden_hits` after every query |
| Snake/camel identifier regex scan (outside code/question/paths/technical panel) | ✓ | Same sweep, regex tier; hits must be justified |
| `aria-live` announcements (run start, each tool result, completion) | ✓ | `role="status"` region asserted (`test_console.py`, live announcements on every run) |
| Zero browser-console errors/warnings | ✓ | `console_errors` fixture asserted in every test (aborted-route noise filtered where intentionally produced) |
| Exactly one `POST /api/chat` + one SSE stream per run | ✓ | `results.json` `apiChatRequests: 1` / `sseStreams: 1` on all 20 captures |

## Defects found and fixed (this pass)

| Defect | Fix |
|---|---|
| Aborted SSE stream left an error card with no recovery path | "Try again" button resubmits the failed question (`RunView` + `App`) |
| Auto-scroll yanked the view down on every event even after scrolling up | Follow-guard keyed to distance-from-bottom; submit re-engages (F8) |
| Composer over-limit validation was unreachable (`maxLength` clipped input first) | `maxLength` removed; validation copy exercised at 2 001 chars |
| Stub `write_file` dropped the requested directory (`reports/…` → `briefing.md`) | Stub honours a backticked `dir/file` target |
| Escape paths echoed in tool args (`C:/Windows/…`) | `pathSummary()` → "a path outside the workspace" |
| Empty web results / zero-tool answers had no designed rendering | Empty-result state + "Answered without tools" note |
| Raw column names and ISO dates in the result table | `columnLabel()` + `humanizeDate()` in the table renderer |
| Live refusal for Q8 was scored as a failure (no Success badges) | Live gate accepts the ideal outcome: clean refusal, zero tool calls, valid final answer + telemetry |

## Environment

- Offline runs: `LLM_PROVIDER=stub`, `SEARCH_PROVIDER=none`, Chromium via
  Playwright, production build.
- Live runs: real `OPENAI_API_KEY` + Tavily (`--live` flag), Q1/Q4/Q8 —
  `results-live.json`, `q01-live.png`, `q04-live.png`, `q08-live.png`.
