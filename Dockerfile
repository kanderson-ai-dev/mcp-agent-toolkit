# syntax=docker/dockerfile:1

# ── Frontend: build the React console ─────────────────────────────────
FROM node:22-bookworm-slim AS frontend

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# vite.config.ts embeds the dataset's uiExample questions at build time.
COPY evaluation/dataset.json /build/evaluation/dataset.json
RUN npm run build

# ── Builder: resolve the lockfile and install into a venv ─────────────
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Dependencies first — this layer only rebuilds when the manifest or
# lockfile changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Then the project itself (package + runtime scripts + prompt root +
# the evaluation package — the web adapter imports it for A/B runs and
# /api/status reads its committed scorecard).
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
COPY scripts/ scripts/
COPY prompts/ prompts/
COPY evaluation/ evaluation/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ── Runtime: slim image, non-root, no build tooling ───────────────────
FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="mcp-agent-toolkit" \
      org.opencontainers.image.description="MCP server + agent client + web console: guarded tools over an open protocol" \
      org.opencontainers.image.authors="Kaarsten Anderson <contact@kaarstenanderson.com>" \
      org.opencontainers.image.licenses="MIT"

RUN groupadd --system app && useradd --system --gid app --uid 10001 app \
    && mkdir -p /data && chown app:app /data

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src
COPY --from=builder --chown=app:app /app/scripts /app/scripts
COPY --from=builder --chown=app:app /app/prompts /app/prompts
COPY --from=builder --chown=app:app /app/evaluation /app/evaluation
COPY --from=frontend --chown=app:app /build/frontend/dist /app/frontend/dist

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    SANDBOX_ROOT=/data/sandbox \
    DB_PATH=/data/demo.db \
    PROMPT_AUDIT_PATH=/data/prompt-registry/audit.jsonl \
    PROMPT_AB_RESULTS_DIR=/data/prompt-registry/ab

USER app

# The stdio transport has no HTTP to probe; when METRICS_PORT is set the
# /healthz endpoint is the real liveness signal, otherwise the container
# being up is it.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import os,sys,urllib.request; p=os.environ.get('METRICS_PORT'); sys.exit(0) if not p else sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz',timeout=2).status==200 else 1)"

CMD ["python", "-m", "mcp_toolkit.server"]
