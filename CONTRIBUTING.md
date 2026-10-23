# Contributing

## Prerequisites

- Python ≥ 3.11 (3.12 recommended) and [`uv`](https://docs.astral.sh/uv/)
- Docker — only for the container build
- Node.js ≥ 20 — only for the React console (a later phase)

## Setup

```bash
uv sync                    # installs the locked dev environment
cp .env.example .env       # optional — the whole suite runs offline with zero secrets
uv run python -m scripts.seed_db   # seed data/demo.db (fixture reports)
```

Run the end-to-end demo (deterministic, offline):

```bash
uv run python -m scripts.demo
```

## Verification block

Run once per change — CI enforces all of it:

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict src
uv run pytest -q --cov=src --cov-branch --cov-fail-under=92
uv run pytest tests/e2e -q                      # needs a frontend build (npm run build)
uv lock --check
uv run pip-audit -r requirements.txt            # regen via: uv export --frozen --no-emit-project
gitleaks detect --no-banner
npm --prefix frontend run lint && npm --prefix frontend run typecheck
npm --prefix frontend test -- --run && npm --prefix frontend run build
docker build -t mcp-agent-toolkit:check .   # when Dockerfile/compose changed
```

Everything runs **offline by default** (`StubLLM` +
`SEARCH_PROVIDER=none`). Tests that need real credentials are gated
behind env vars and never run in CI.

## Conventions

- TypeScript oracle at `../mcp-agent-toolkit` is the behavioral spec —
  port tests one-to-one; when behavior drifts, fix it in the backend,
  not the test.
- `ruff` line-length 100, rule sets `E,F,I,UP,B,SIM,S,ASYNC`;
  `mypy --strict` on `src/` — zero errors.
- Errors cross the wire as typed envelopes (`isError=true`), never as
  thrown protocol errors.
- Logs go to **stderr** (structlog JSON); stdout is reserved for the MCP
  transport and the CLI event stream.
- No secrets in code, tests, fixtures, commits, or docs — `.env` is
  gitignored; check `git status` before committing.

## How to change a prompt (always through the registry)

Never edit a promoted YAML in place — prompts are versioned artifacts:

1. **Author a candidate** — write the new template to a file, then
   `uv run python -m scripts.prompts_cli candidate agent_system --file
   <file> --description "<what changed>"` (or
   `POST /api/prompts/agent_system/candidates` with
   `ENABLE_PROMPT_ADMIN=1`). Templates are *data*: schema-validated,
   size-capped, rejected if they contain key-looking strings.
2. **Measure** — `… prompts_cli ab agent_system --candidate <v>` runs the
   live A/B gate (real model + judge) and records the result. Offline
   runs are mechanics smoke only and can never promote.
3. **Promote or roll back** — `… prompts_cli promote agent_system
   --version <v>` / `rollback agent_system --to-version <v> --reason
   "…"`. Every mutation lands in the hash-chained audit log
   (`data/prompt-registry/audit.jsonl`); `… prompts_cli audit` verifies
   the chain. Inspect any version with `show` / `diff` first.

## How to add a tool

1. **Handler** — `src/mcp_toolkit/server/tools/<name>.py`: a pure
   `async def handler(**args, ctx=ToolContext) -> dict` that returns the
   `data` payload; raise `ToolError(code, msg, guardrail=...)` for typed
   failures. Register it in `build_tools()`.
2. **MCP surface** — add a thin `@server.tool` wrapper in
   `server/app.py` delegating to `guarded_invoke`; annotate params with
   `pydantic.Field` (bounds become the JSON schema).
3. **Stub path** — teach `StubLLM` a plan step if the demo should call it.
4. **Tests** — unit tests for the handler (no network), plus the stdio
   integration suite for discovery/invocation; guardrail rejections get
   their own security test that must fail loudly.

## Commit style

Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`),
atomic, professional English, no tool or generator attribution.
