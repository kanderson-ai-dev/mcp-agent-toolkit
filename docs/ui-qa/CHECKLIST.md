# Visual QA checklist

Self-evaluation of the web console against the visual checklist. Every row was
verified on the **production build** (`npm run build` → `frontend/dist`) in a
real Chromium via Playwright. Automated checks are in
[`scripts/ui_qa.py`](../../scripts/ui_qa.py); per-run measurements are in
[`results.json`](results.json) (offline) and [`results-live.json`](results-live.json)
(live OpenAI + Tavily). All screenshots below were inspected visually, not only
in the DOM.

## §A — per viewport (1280×800 / 768×1024 / 375×812)

| ID | Check | 1280 | 768 | 375 | Evidence |
|---|---|---|---|---|---|
| V1 | Four designed states styled | ✓ | — | — | `state-empty.png`, `state-empty-light.png`, `state-streaming.png`, `state-result.png`, `state-error.png` |
| V2 | No raw field names visible; technical JSON collapsed | ✓ | ✓ | ✓ | Forbidden-literal sweep after every query (`test_ui_qa.py::_sweep`); raw JSON behind "View raw JSON" |
| V3 | Contrast ≥ 4.5:1 body / ≥ 3:1 large text & badges | ✓ | ✓ | ✓ | `results.json` `contrastFailures: []` on all 20 captures; gradient headline measured via gradient stops |
| V4 | Consistent spacing scale, no arbitrary `[Npx]` spacing | ✓ | — | — | Grep clean except `p-[1px]` ring-inset wrapper (`EmptyState`) and `text-[10px]`/`text-[11px]` chip labels — sub-scale *type*, not spacing; deliberate |
| V5 | CLS < 0.02 while streaming | ✓ | ✓ | ✓ | `results.json` `cls` ≤ 0.0088 everywhere (was 0.021–0.027 before the working indicator moved into the composer) |
| V6 | No horizontal scroll; tap targets ≥ 44 px at 375; composer reachable | ✓ | ✓ | ✓ | `horizontalOverflowPx: 0` on all captures; `tapTargetsBelow44px: []` on all 375px runs |
| V7 | Icons vertically centred, consistent size | ✓ | ✓ | ✓ | Manual inspection: lucide icons centered in badges/cards (14 px in compact badges, 16/20 elsewhere — consistent within each component class) |
| V8 | One type scale; monospace code/JSON; markdown spaced; long URLs wrap | ✓ | ✓ | ✓ | `q01-*.png`, `q04-desktop.png`, `q06-phone.png` — Tailwind type scale, `font-mono` for SQL/paths/JSON |
| V9 | Long paths, wide cells, 12 k-char output contained | ✓ | ✓ | ✓ | `q06-phone.png` (file preview wraps inside card); args summaries clip with ellipsis; server caps output at `MAX_TOOL_OUTPUT_CHARS` with a humanised truncation note |
| V10 | `prefers-reduced-motion` respected; spinners stop on completion | ✓ | — | — | Global reduce-motion rule in `index.css` collapses all animations to ~0 ms; Running badge → Success on completion |
| V11 | Visible focus ring; logical tab order; disclosures keyboard-operable | ✓ | — | — | `focus-visible` rings on toggles/buttons, `focus-within` ring on composer; native buttons + Radix tabs/disclosures; dialogs close on Esc |
| V12 | Designed empty states (zero rows, empty search, no tools, empty audit) | ✓ | — | — | `state-empty.png` (example chips), `q04-desktop.png` (source list), `q09-*.png` ("Answered without tools"), registry empty states in `test_registry.py` |
| V13 | Sentence-case copy; no jargon; consistent tense | ✓ | — | — | Copy audit across captures — "MCP"/"SSE" never appear in control copy (only the user-typed question and the subtitle's "standard protocol") |
| V14 | Light and dark themes both pass V3 | ✓ | — | — | `state-empty-light.png` captured in light theme with `contrastFailures: []`; tokens flip via `data-theme` |
| V15 | **[REG]** Registry diff readable at 375 px; blocked-promote explanation plain; audit ordered; "Chain verified" badge | ✓ | — | ✓ | `registry/` captures + `test_registry.py` — diff scrolls inside its own box; gate failures render as sentences |

## Automated check results (`results.json`)

20/20 captures pass: `ok: true`, `horizontalOverflowPx: 0`, `cls` ≤ 0.0088,
`contrastFailures: []`, `tapTargetsBelow44px: []` on every 375 px run, and
exactly one `POST /api/chat` plus one SSE stream per query
(`apiChatRequests: 1`, `sseStreams: 1`).

Live run (`results-live.json`): Q1, Q4 and Q8 executed against real OpenAI and
Tavily. Q8's clean refusal with zero tool calls is accepted as the ideal secure
outcome (no guardrail trip needed) — the final answer and telemetry rendered
correctly (`q08-live.png`).

## Defects found and fixed (this pass)

| Defect | Fix |
|---|---|
| "The agent is working…" indicator sat at the timeline's end; every incoming card displaced it → CLS 0.021–0.027 on live runs | Indicator moved into the composer's hint line (outside the dynamic flow); CLS now ≤ 0.009 |
| Tap targets < 44 px at 375 px (send 40, theme toggle 32, disclosures ~16, chips) | `max-sm:` sizing brings all interactive controls ≥ 44 px on phone |
| Contrast checker reported the gradient hero headline as 1.0 (`color: transparent` + `background-clip: text`) | Checker now measures the gradient's colour stops against the background — real ratios, not a false positive |
| Sticky composer's translucent backdrop smeared across full-page screenshots | Query/state captures shoot the run `section` element with the composer hidden — evidence shows the actual subject |
| "Running" badge window is sub-millisecond at stub latencies → flaky streaming capture | Deterministic in-flight frame: the chat POST is held open ~1.5 s so the capture shows question + disabled composer + working indicator; badge styling is pinned by `ToolStepCard.test.tsx` |
| Auto-scroll yanked the view to the bottom on every event, even after the reader scrolled up | Follow-guard: auto-scroll only while within 96 px of the bottom; a new submit re-engages (F8) |
| No `prefers-reduced-motion` handling anywhere | Global media query in `index.css` collapses animations/transitions to ~0 ms (V10) |
| Browser-native path separators leaked into sandbox-relative paths (`notes\console-check.md`) | `read_file`/`write_file` return POSIX separators — sandbox paths are virtual, not OS paths |
| Raw snake_case column names (`published_at`) as table headers | `columnLabel()` humanises headers ("Published At"); ISO dates localised |
| External result links with `rel="noreferrer"` only | `rel="noopener noreferrer"` on all external links |
| Escape paths (`C:/Windows/…`, `../../etc/passwd`) echoed verbatim in tool args | `pathSummary()` renders them as "a path outside the workspace" (raw value stays in collapsed JSON only) |
| No designed state for empty web results / zero-tool answers | "No matching pages found" empty state; "Answered without tools" note |
| Composer had no visible validation messages and `maxLength` made the over-limit path unreachable | "Questions need at least 3 characters." / "Keep it under 2 000 characters."; `maxLength` removed so the over-limit branch is exercised |
| Stub `write_file` ignored the requested destination path | Stub honours a backticked `dir/file` target; bare filenames are never mistaken for write targets |

## Manual inspection log

Every capture in this directory was opened and read as an image. Notable
observations: `q03` shows the recovery arc (Error → Success with a human
message); `q07`/`q08` show the "Blocked by security policy" state with no raw
codes; `q09` shows the "Answered without tools" note; `q01-live.png` and
`q04-live.png` show real Tavily hits and real cost chips; `q08-live.png` shows
the model refusing without attempting any call; the fade-up animation can be
caught mid-flight in live captures (intended motion, not a defect).
