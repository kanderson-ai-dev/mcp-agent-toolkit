# Console design tokens

The console is theme-agnostic at the component layer. Components never
name a palette color for surfaces or text; they consume the semantic
`--color-*` utilities generated from the tokens below, defined in
`frontend/src/index.css` (`@theme inline` → `--t-*` custom properties).
`[data-theme="light"]` re-declares the same `--t-*` variables, so every
component flips without a single class change.

## Theme resolution

- `index.html` runs a tiny inline script before first paint: saved
  `localStorage.theme` wins, else `prefers-color-scheme`, else **dark**
  (the product default). `color-scheme` is set on `<html>` so native
  controls (scrollbars, form fields) follow the theme.
- `ThemeToggle` (header, far right) flips `data-theme` and persists the
  choice. It is a `<button>` — keyboard focusable, `aria-label` states
  the *destination* theme ("Switch to light theme").
- `prefers-reduced-motion` is respected: the only animations are a
  350 ms fade-up on new cards, a subtle ping on the live indicator and a
  spinner on the Running badge — no flashing.

## Token palette

| Token | Role | Dark | Light |
|---|---|---|---|
| `page` | App background, code/pre wells | slate-950 | slate-50 |
| `raised` | Cards, panels, composer | slate-900 | white |
| `overlay` | Chips, question bubble, table head, code bg | slate-800 | slate-200 |
| `line` / `line-strong` | Borders and rings (subtle / strong) | slate-800 / 700 | slate-200 / 300 |
| `ink` | Primary text | slate-100 | slate-900 (14.7:1) |
| `soft` | Secondary text, code text | slate-300 | slate-700 (8.6:1) |
| `muted` | Captions, meta text | slate-400 | slate-600 (5.9:1) |
| `faint` | List markers, disabled | slate-500 | slate-500 (4.8:1) |
| `accent` | Brand/interactive (links' sibling, icons, focus) | indigo-300 | indigo-600 (6.0:1) |
| `ok` | Success badges/dots | emerald-300 | emerald-700 (5.0:1) |
| `danger` | Error badges/banners | rose-300 | rose-700 (6.7:1) |
| `warn` | Warnings (demo mode, truncation, step limit) | amber-300 | amber-700 (5.0:1) |
| `cyan` / `violet` / `sky` | Tool accents (web search / DB / running) | *-300 | *-700 (≥5:1) |

Text tokens keep ≥ 4.5:1 contrast on their surfaces in **both** themes
(values verified against WCAG AA). Status is never conveyed by color
alone — every badge pairs color with an icon and a text label
("Success", "Error", "Running").

Hue-tinted translucent surfaces (`bg-rose-500/10`, `ring-rose-500/30`,
`bg-indigo-500/15`) intentionally keep their raw hue: the alpha blends
into each theme's background — a pale tint on light, a glow on dark —
while the *text* on them is always a semantic token.

## Spacing, type, iconography

- Spacing uses the Tailwind default scale (4 px base) — `p-3`, `gap-2`,
  `mt-4` — with no arbitrary `[Npx]` values in components.
- Type scale: `text-xs` (meta/badges) → `text-sm` (body) →
  `text-2xl/3xl` (hero). Mono (`--font-mono`, JetBrains Mono) for SQL,
  paths, JSON and trace ids.
- Lucide icons at 14/16/18 px (`size-3.5`, `size-4`, `size-4.5`),
  vertically centred inside fixed-size tiles.
- The hero headline uses `.hero-title`, a token-driven gradient
  (`accent → ink → cyan`), not fixed hex stops.

## Accessible states

- Focus: `focus-visible` outlines on interactive elements
  (`outline-accent`), `:focus-within` ring on the composer.
- Live region: a `role="status"` (`aria-live="polite"`) visually-hidden
  div announces "Run started", each tool result ("Web search finished:
  success") and completion — never raw tool ids.
- Disclosure controls (`aria-expanded`): result panels and "View raw
  JSON" are collapsed by default so no raw field names are visible until
  the user asks for them.
