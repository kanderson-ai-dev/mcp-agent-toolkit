# Contributing

Setup and verification — expanded in later phases.

## Prerequisites

- Python ≥ 3.11 (3.12 recommended) and [`uv`](https://docs.astral.sh/uv/)
- Node.js ≥ 20 — only for the React console (`frontend/`)

## Setup

```bash
uv sync
cp .env.example .env   # optional; everything runs offline with zero secrets
```

## Verification block

Run once per change, as relevant:

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict src
uv run pytest -q --cov=src --cov-branch --cov-fail-under=90
uv run python -m evaluation.run_eval --offline
uv run pytest tests/e2e -q                 # Playwright, offline
npm --prefix frontend run lint && npm --prefix frontend run typecheck
npm --prefix frontend test -- --run && npm --prefix frontend run build
gitleaks detect --no-banner
uv run pip-audit
```

Conventions: `ruff` line-length 100 (`E,F,I,UP,B,SIM,S,ASYNC`), `mypy
--strict`, tests are offline and deterministic by default — live-credential
tests are gated behind `RUN_LIVE_TESTS=1` and the `live` marker.
