# Contributing

## Setup

```bash
npm ci
npm run seed:db        # creates data/demo.db (fixture for db_query)
cp .env.example .env   # optional — the suite and StubLLM need no secrets
```

## Development commands

```bash
npm run lint           # eslint, zero warnings
npm run typecheck      # tsc --noEmit, strict — zero errors
npm test               # vitest suite (fully offline)
npm run test:cov       # same + coverage (thresholds: 85% all metrics)
npm run build          # compile to dist/
npm run agent -- "q"   # run the agent against the real MCP server
npm run demo           # seeded E2E demo, saves docs/demo-transcript.txt
npm run demo:record    # record docs/demo.cast → render GIF via docker + agg
```

## Conventions

- TypeScript ESM, `strict` everywhere — no `any` without a comment.
- Tool handlers throw `ToolError`; the server wrapper converts them to
  error envelopes — never return a thrown error shape yourself.
- Everything returned by a tool is **untrusted data**: sanitize before it
  reaches the model. Never interpolate tool output into instructions.
- Tests must be deterministic and offline: no network, no real API keys.
  Use the in-memory MCP transport or spawned stdio fixtures.
- Commits: Conventional Commits, atomic, no AI-attribution trailers.

## Pull request checklist

1. `npm run lint && npm run typecheck && npm run test:cov` — all green.
2. Coverage thresholds hold (≥85% lines/branches/functions/statements).
3. New tool? Schema-validated input, sandbox/rate-limit wiring,
   sanitization, and a security test proving the failure mode.
4. No secrets, credentials, or real key material anywhere — CI runs
   gitleaks.
