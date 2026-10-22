# Phase 8 self-assessment — §A visual checklist

Evidence: screenshots in this directory, captured against the real
backend (`LLM_PROVIDER=stub SEARCH_PROVIDER=none`, `uv run mcp-toolkit-web`,
one port) with headless Chromium. Viewports: 1280×800, 768×1024,
375×812.

| ID | 1280 | 768 | 375 | Evidence / note |
|---|---|---|---|---|
| V1 states styled | ✓ | ✓ | ✓ | `01-empty-*`, `02-streaming-1280`, `03-result-expanded`, `06-guardrail-block`, `07-api-down` |
| V2 no raw field names visible | ✓ | ✓ | ✓ | Labels dictionary covers every tool/status/code; technical JSON collapsed by default (`04-technical-json`) |
| V3 contrast ≥4.5:1 | ✓ | ✓ | ✓ | Text tokens AA-verified both themes (`docs/ui-qa/DESIGN.md`); status = icon + text, never color alone |
| V4 4/8 spacing | ✓ | ✓ | ✓ | Tailwind default scale only; `grep '\[\d+px\]' src/` → 0 hits |
| V5 no layout jump | ✓ | — | — | Cards fade-in with reserved space; scroll follows bottom anchor |
| V6 no h-scroll, tap targets | ✓ | ✓ | ✓ | Composer + cards fluid to 375 px; buttons ≥40 px |
| V7 icon alignment | ✓ | ✓ | ✓ | Fixed-size tiles, centered Lucide icons |
| V8 type scale + markdown | ✓ | ✓ | ✓ | `text-xs/sm/2xl` scale; mono for SQL/JSON; hand-tuned `.markdown` styles |
| V9 overflow containment | ✓ | — | — | `truncate`, `line-clamp`, `max-w-*`, scrollable pre/table inside cards |
| V10 reduced motion | ✓ | — | — | Only fade-up/ping/spinner; spinners stop on completion |
| V11 focus + keyboard | ✓ | ✓ | ✓ | `focus-visible` outlines; disclosures are `<button aria-expanded>`; theme toggle is a button |
| V12 empty/zero states | ✓ | — | — | Empty web results, no-tools answer, offline banner all designed |
| V13 sentence-case copy | ✓ | ✓ | ✓ | No jargon in primary copy ("Demo mode", "Can't reach the agent.") |
| V14 both themes pass V3 | ✓ | — | — | `01b-empty-light`, `05-light-theme`; toggle persists via localStorage |
| V15 [REG] registry tab | n/a | n/a | n/a | Prompt Registry arrives in Phase 13 |

**Defects found and fixed during the capture pass**

1. Headless Chromium honors `prefers-color-scheme: light`, revealing the
   toggle label flips correctly — no defect, but it confirmed the
   no-JS theme resolution works.
2. "Demo mode" badge wrapped into a two-line pill at 375 px → fixed with
   `whitespace-nowrap` (re-captured `01-empty-375.png`).
3. `npm run web` copy from the TypeScript oracle → `uv run mcp-toolkit-web`.
4. Missing `aria-live` run announcements → `role="status"` region added.
5. Connection banner lacked the required "Try again" action → added.
6. Technical-details panel had no copy affordance → "Copy JSON" button
   copies the exact pretty-printed payload.

Screenshot index: `01-empty-{1280,768,375}` (dark), `01b-empty-light-1280`,
`02-streaming-1280`, `03-result-expanded-1280`, `04-technical-json-1280`,
`05-light-theme-1280` (finished run, toggled), `06-guardrail-block-1280`
(adversarial DROP TABLE → "Blocked by security policy"),
`07-api-down-1280` ("Can't reach the agent." + Try again).
