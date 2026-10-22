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
uv run pytest -q --cov=src --cov-branch --cov-fail-under=90
uv lock --check
uv run pip-audit
gitleaks detect --no-banner
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
