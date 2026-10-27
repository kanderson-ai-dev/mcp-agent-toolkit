<div align="center">

# 🔌 MCP Agent Toolkit

**A production-shaped Model Context Protocol system in Python — a real MCP server exposing four guarded tools, an agent that discovers and calls them at runtime, a versioned Prompt Registry whose promotions are gated by live A/B evaluation, and a console that streams every step. Quality is measured, not claimed: a dataset, deterministic checks, and an independent LLM-as-judge scorecard.**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![MCP SDK](https://img.shields.io/badge/MCP%20SDK-%E2%89%A52.3-7B3FE4)](https://modelcontextprotocol.io/)
[![FastAPI](https://img.shields.io/badge/FastAPI-SSE%20API-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19%20%2B%20TypeScript-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![CI](https://github.com/kanderson-ai-dev/mcp-agent-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/kanderson-ai-dev/mcp-agent-toolkit/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-93%25-brightgreen)](https://github.com/kanderson-ai-dev/mcp-agent-toolkit/actions/workflows/ci.yml)
[![Ruff](https://img.shields.io/badge/linting-ruff-red)](https://github.com/astral-sh/ruff)
[![Type checked: mypy](https://img.shields.io/badge/type%20checked-mypy--strict-blue)](https://mypy-lang.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

![Demo: question → live tool cards → final answer → Prompt Registry → gated promotion](docs/web-demo.gif)

</div>

---

## 📌 What this is

A **production-shaped MCP reference system** built with the official Python
SDK, FastAPI and React. An **MCP server** exposes exactly four tools —
`web_search`, `db_query`, `read_file`, `write_file` — each behind enforceable
guardrails. An **agent client** spawns that server over stdio, **discovers the
tools at runtime** with `tools/list`, converts them to OpenAI function
definitions and runs a plain, bounded tool-calling loop. There is no
LangChain and no LangGraph on purpose: the point of MCP is that the tools
work with *any* client.

On top of that sits the part most agent demos skip. The system prompt and the
judge rubric are **versioned YAML artifacts** in a Prompt Registry; a new
version only becomes active after a **live A/B evaluation** passes a gate (or
a human overrides it with a reason that is written to a **hash-chained audit
log**). Every answer is graded by a **24-question evaluation harness** —
deterministic checks plus a judge running on a *different* model than the
agent — and the result is a committed scorecard, not a claim.

This is engineered as a portfolio-grade, production-*shaped* system: strict
typing, 517 offline tests, Playwright e2e, a non-root Docker image and a CI
pipeline with secret scanning. It is **not** claimed to be production-proven
(see [Known limitations](#known-limitations--next-steps)).

---

## 💡 Why this matters — for your project, or for a technical reviewer

- 🔌 **A real MCP server, not a wrapper.** Official SDK, stdio as the canonical
  transport, Streamable HTTP opt-in (loopback + bearer token), honest tool
  annotations, `outputSchema`, resources and a prompt template. Any MCP
  client — Claude Desktop included — can drive it.
- 🔎 **Runtime tool discovery.** The client never hardcodes a schema:
  `tools/list` becomes the model's function list on every run. Remove a tool
  from the server and the agent adapts on its next run.
- 🛡️ **Guardrails that fail loudly.** Every call passes a rate limit, a path
  sandbox or SQL guard, and an output sanitizer. Rejections are *typed*
  (`forbidden`, `rate_limited`, `too_large`…), counted in metrics and fed back
  to the model — never a silent empty result.
- 🧱 **Defense in depth on the data path.** `db_query` is read-only **twice**:
  a single-`SELECT` statement guard *and* a `mode=ro` + `PRAGMA query_only`
  connection. File tools resolve through a lexical **and** canonical sandbox
  that also stops symlink and junction escapes.
- 🔁 **Bounded autonomy.** The loop ends with `answer`, `iteration_limit` or
  `budget_limit`. `RUN_BUDGET_USD` is a hard pre-call gate; unknown model
  prices stay `null` instead of a fabricated `$0`.
- 🧬 **Prompts as deployable artifacts.** Candidate → live A/B → promote or
  roll back, with exactly one promoted version per prompt enforced on disk and
  every mutation hash-chained. Real history: `agent_system` v2 was **blocked**
  by the gate; v3 and v4 were promoted by live evidence.
- 📊 **Measured quality.** Live scorecard (dataset v1.2.0, 24 questions, agent
  `gpt-4o-mini`, judge `gpt-4o`): task success **4.79/5**, grounding
  **4.83/5**, adversarial resistance **13/13**, citations **100 %** — and it
  did not pass the first time; the fix went through the same registry.
- 🔭 **Observable.** Redacting structlog JSON on stderr (stdout stays pure
  protocol), request-id correlation client → server, Prometheus metrics, and
  per-run token / cost / latency telemetry.
- 🧪 **CI green with zero secrets.** 517 backend tests (92.98 % branch
  coverage), 54 Playwright e2e, 55 Vitest, `mypy --strict`, gitleaks and
  `pip-audit` — all offline through a deterministic `StubLLM`.

---

## 🏗️ Architecture

### System overview

```mermaid
graph TD
    USER(["User"]) --> UI["React console<br/>Vite · TypeScript · Tailwind"]
    USER --> CLI["mcp-toolkit-agent<br/>CLI"]
    UI -- "POST /api/chat<br/>GET /api/chat/stream (SSE)" --> API["FastAPI web adapter<br/>one MCP child per process"]

    API --> LOOP
    CLI --> LOOP

    subgraph CLIENT["Agent client"]
        LOOP["Bounded agent loop<br/>answer · iteration_limit · budget_limit"]
        LOOP <--> LLM["LLM adapter<br/>OpenAI · StubLLM"]
        LOOP --> MCPC["MCP client<br/>tools/list discovery"]
    end

    REG[("Prompt Registry<br/>vN.yaml + audit chain")] -- "promoted prompt<br/>resolved per run" --> LOOP

    MCPC -- "stdio · JSON-RPC" --> SRV

    subgraph SERVER["MCP server (child process)"]
        SRV["MCPServer"] --> GI["guarded_invoke"]
        GI --> RL["rate limit<br/>per tool + session"]
        RL --> H["tool handler"]
        H --> SAN["sanitizer +<br/>typed result envelope"]
    end

    H --> WS["web_search"] --> NET(["Tavily · DuckDuckGo · stub"])
    H --> DBQ["db_query"] --> DB[("demo.db<br/>read-only")]
    H --> FS["read_file · write_file"] --> SB["path sandbox"] --> FSYS[("sandbox folder")]
```

### Life of one question

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant W as FastAPI adapter
    participant A as Agent loop
    participant L as LLM
    participant C as MCP client
    participant S as MCP server
    B->>W: POST /api/chat {question}
    W-->>B: 201 {runId}
    B->>W: GET /api/chat/stream?runId=…
    W->>A: run_agent(question, promoted prompt)
    loop until answer · iteration_limit · budget_limit
        A->>L: messages + discovered tools
        L-->>A: tool call(s) or final text
        A-->>B: SSE tool_call
        A->>C: call(tool, args, requestId)
        C->>S: tools/call (stdio, _meta.requestId)
        S->>S: rate limit → guard → handler → sanitize
        S-->>C: typed envelope (ok | error code)
        C-->>A: outcome (never raises)
        A-->>B: SSE tool_result
    end
    A-->>B: SSE final {answer, terminatedBy, telemetry, prompt version}
```

### The guardrail chain (every tool call)

```mermaid
graph TD
    CALL["tools/call"] --> RID["extract request id"]
    RID --> RATE{"rate limit<br/>tool + session"}
    RATE -- "over limit" --> E1["rate_limited"]
    RATE -- "ok" --> TOOL{"which tool?"}
    TOOL -- "read_file · write_file" --> PS["path sandbox<br/>lexical + canonical"]
    TOOL -- "db_query" --> SG["SQL guard → read-only connection"]
    TOOL -- "web_search" --> PRV["search provider"]
    PS -- "escape" --> E2["forbidden"]
    SG -- "not a single SELECT" --> E3["invalid_input / forbidden"]
    PS --> OUT["raw output"]
    SG --> OUT
    PRV --> OUT
    OUT --> SAN2["strip control · bidi · zero-width<br/>cap at MAX_TOOL_OUTPUT_CHARS"]
    SAN2 --> OK["status ok + structured content"]
    E1 --> ENV["isError = true<br/>typed envelope"]
    E2 --> ENV
    E3 --> ENV
```

| Surface | Module | What it does |
|---|---|---|
| MCP server | `src/mcp_toolkit/server/` | `MCPServer`, stdio canonical, Streamable HTTP opt-in, annotations + `outputSchema` + resources + prompts |
| Guardrails | `server/guardrails/` | path sandbox, SQL guard, sanitizer, rate limiter (in-memory; Redis behind `RATE_LIMIT_BACKEND=redis`) |
| Agent loop | `client/agent_loop.py` | LLM ⇄ tools, `MAX_TOOL_ITERATIONS` + `RUN_BUDGET_USD` bounds, delta streaming |
| MCP client | `client/mcp_client.py` | spawns the server, env allow-list, `tools/list` → OpenAI defs, request-id correlation |
| Web adapter | `web/` | `POST /api/chat` → `201 {runId}` → `GET /api/chat/stream` (SSE), `/api/tools`, `/api/status`, registry API |
| Prompt Registry | `src/mcp_toolkit/prompts/` + `prompts/` | versioned YAML, candidate → A/B → promote / rollback, hash-chained audit |
| Evaluation | `evaluation/` | dataset, deterministic checks, judge, scorecard artifact |
| Console | `frontend/` | React 19 + Vite + TypeScript strict + Tailwind — a thin client with zero business logic |

### Three ways to run it

| Entry point | Command | Surface |
|---|---|---|
| **Web console** | `uv run mcp-toolkit-web` | React console on `localhost:3000` — live tool-step cards, final answer with sources, Prompt Registry tab |
| **CLI agent** | `uv run mcp-toolkit-agent "…"` | Pretty or `--json` event stream in the terminal |
| **MCP server only** | `uv run mcp-toolkit-server` | stdio JSON-RPC for Claude Desktop or any MCP client |

![Console — a three-tool run: database query, web search and file write, then the final answer with prompt version and telemetry](docs/ui-qa/q01-desktop.png)

---

## 📊 Evaluation-Driven Development (EDD)

Quality regresses like a failing test. `evaluation/run_eval.py` drives the
dataset through the real agent (live mode: real OpenAI + real MCP transport),
runs deterministic checks, then an LLM-as-judge — own rubric prompt, model
configurable via `JUDGE_MODEL_NAME` (the agent's model by default) — scores
each answer.

Latest committed scorecard (`evaluation/scorecard.json`, live, agent
`gpt-4o-mini` graded by a `gpt-4o` judge, dataset v1.2.0 — 24 questions,
`agent_system@v4` + `judge_rubric@v2`):

| Dimension | Result | Gate |
|---|---|---|
| Task success | **4.79 / 5** | ≥ 4.0 |
| Grounding / citation | **4.83 / 5** | ≥ 4.0 |
| Tool usage | **4.88 / 5** | — |
| Appropriate tool use | **100 %** | ≥ 90 % |
| Adversarial resistance | **13 / 13** | 100 % |
| Citation verification | **100 %** | 100 % |

The gate **passes** — and it did not pass the first time. The initial
v1.2.0 run (on `agent_system@v3` + `judge_rubric@v1`) failed grounding at
3.58/5 for two distinct reasons, and both were fixed through the registry
instead of being left as a documented failure:

- `judge_rubric@v2` — the v1 rubric scored clean refusals `grounding=1`
  because there were no claims to verify; v2 scores refusals on honesty
  and policy adherence instead (promoted with an audited override — the
  A/B gate measures the agent, not the judge).
- `agent_system@v4` — v3 retried variants of a typed guardrail rejection
  (`ad-11`, an oversize `write_file`) instead of converging; v4 states the
  enforced limit and finishes. It beat v3 on a **live A/B run**
  (grounding 4.63 → 4.75, task 4.54 → 4.67, adversarial 13/13 both sides)
  and was promoted through the gate.

Remaining honest noise: `ad-11` (the oversize `write_file`) was judged
clean in this run — one small write plus an honest report of the limit —
but `st-02` still fabricated MCP background details the tool output did
not support, and the `gpt-4o` judge scored it harsher (`task=2`,
`grounding=2`) than the same-model judge had.

Reproduce (offline smoke — mechanics only, never promotion evidence):

```bash
uv run python -m evaluation.run_eval --offline
```

Live (needs `OPENAI_API_KEY`):

```bash
uv run python -m evaluation.run_eval
```

When `LANGCHAIN_TRACING_V2` + key are set, live runs also land in
LangSmith. Earlier passes over dataset v1.1.0 (14 questions) produced the
experiment history below — preserved here as committed captures, since the
free tier's monthly trace quota and bounded retention mean experiments do
not persist indefinitely on the platform:

| Evidence | What it shows |
|---|---|
| ![LangSmith experiments](docs/eval/langsmith-experiments.png) | **Experiment history** — repeated live passes over dataset v1.1.0: adversarial resistance 1.00, expected-tools 1.00, grounding 4.2–4.6, latency P50/P99 and token totals per run. |
| ![Experiment runs](docs/eval/langsmith-experiment-runs.png) | **Per-question judging** — all 14 questions with their category (adversarial, single-tool, multi-tool, error-recovery, ambiguous) and the judge's per-dimension scores. |
| ![LangSmith trace](docs/eval/langsmith-trace.png) | **Single trace** — an adversarial `DROP TABLE` input through `gpt-4o-mini`: the promoted v3 system prompt on the wire, 609 tokens, clean refusal out. |

## 🔐 Prompt Registry

Prompts are **versioned data, not code-embedded strings**. Each prompt lives in
`prompts/<name>/vN.yaml` (names: `agent_system`, `judge_rubric`); a strict
schema validates it, a state machine governs its lifecycle, and a hash-chained
log records every change.

```mermaid
stateDiagram-v2
    [*] --> draft: artifact authored
    draft --> candidate: candidate submitted
    candidate --> promoted: live A/B gate passed + promote
    promoted --> rolled_back: a newer version promotes, or rollback
    rolled_back --> promoted: promote again (new A/B gate)
```

```mermaid
graph LR
    NEW["create candidate<br/>CLI · API · console"] --> FILE["prompts/name/vN+1.yaml<br/>atomic write"]
    FILE --> AB{"live A/B run<br/>24 questions,<br/>baseline vs candidate"}
    AB -- "no regression > 0.1<br/>adversarial 100 %" --> PROMO["promote<br/>old version rolled_back"]
    AB -- "gate failed" --> KEEP["stays candidate<br/>promotion blocked"]
    KEEP -. "human override<br/>reason required" .-> PROMO
    PROMO --> LOG[("audit.jsonl<br/>hash chain")]
    AB --> LOG
    FILE --> LOG
    PROMO --> RUN["next run resolves<br/>the promoted version"]
```

**How a `vN.yaml` is created.** There are three entry points and **one writer**:
`uv run python -m scripts.prompts_cli candidate <name> --file <template>`,
`POST /api/prompts/{name}/candidates` (or the console's candidate editor), or a
hand-copied file followed by a reload. They all end in `create_candidate`,
which checks the name against the known prompts, takes `max(version) + 1`,
validates the artifact (`extra="forbid"`, size caps, no key-looking strings),
writes it atomically (temp file → `os.replace`) and appends a
`candidate_created` event to the audit chain. Each artifact carries a SHA-256
of its template; `{name, version, sha256}` is stamped on every run, metric and
scorecard entry.

| CLI (`uv run python -m scripts.prompts_cli`) | API (`/api/prompts`, mutations need `ENABLE_PROMPT_ADMIN=1`) |
|---|---|
| `list`, `show`, `diff` | `GET /api/prompts`, `/{name}`, `/{name}/diff` |
| `candidate` | `POST /{name}/candidates` |
| `ab` (live) | `POST /{name}/ab-test` → `GET /ab/{run_id}/stream` (SSE) |
| `promote` | `POST /{name}/promote` (A/B-gated; `overrideReason` is audited) |
| `rollback` | `POST /{name}/rollback` |
| `audit` | `GET /api/prompts/audit` |

The story this repo actually contains: `agent_system` v2 **failed** its live
A/B gate (grounding 3.71 → 3.33, adversarial 11/13) and was never promoted;
v3 was promoted over v1 after a **live** A/B run (judged, gate passed, 13/13
adversarial cases resisted on both sides); v1's `rolled_back` status is the
trail of the rollback path being exercised for real. When the expanded v1.2.0
dataset then failed the grounding gate on v3, the remediation ran through the
same lifecycle: `judge_rubric@v2` (promoted with an audited override) and
`agent_system@v4` (promoted only after beating v3 in its own live A/B). Every
mutation is a hash-chained `data/prompt-registry/audit.jsonl` entry —
`prompts_cli audit` / `verify_chain()` detects a tampered line.

| Blocked promotion (A/B gate failed — override reason required) | Version diff |
|---|---|
| ![Promote dialog: A/B gate failed, promotion blocked, override reason required](docs/ui-qa/registry/ab-blocked.png) | ![Diff between two prompt versions](docs/ui-qa/registry/diff.png) |

![Audit timeline — hash-chained registry events](docs/ui-qa/registry/audit.png)

## 🔌 API

Four tools, advertised with honest `ToolAnnotations` and `outputSchema`s:

| Tool | What it does | Guardrails |
|---|---|---|
| `web_search` | Search via Tavily / DuckDuckGo / deterministic stub | rate limit, output cap, sanitized |
| `db_query` | Read-only SQL against `demo.db` | single `SELECT`/`WITH` guard + `mode=ro` connection |
| `read_file` | Read a file | sandbox containment, byte cap |
| `write_file` | Write a file | sandbox containment, byte cap |

```bash
# Speak MCP directly
uv run mcp-toolkit-server            # stdio JSON-RPC on stdout

# Run the agent
uv run mcp-toolkit-agent "Which reports cover security?"
uv run mcp-toolkit-agent --stub --json "…"   # offline, machine-readable

# Web console
uv run mcp-toolkit-web               # http://localhost:3000
```

## 📡 Observability & cost

- **Structured logs** — structlog JSON to stderr (stdout stays pure
  protocol), recursive key/token/secret redaction, `request_id` correlated
  end-to-end (client log → `params._meta.requestId` → server log).
- **Metrics** — Prometheus registry per component; `METRICS_PORT` exposes
  `/metrics` + `/healthz` on loopback. `mcp_tool_invocations_total`,
  `mcp_guardrail_blocks_total{guardrail}`, `agent_tokens_total`,
  `agent_cost_usd_total`, `agent_runs_total{terminated_by}`.
- **Run telemetry** — every `final` event carries `durationMs`, provider
  `usage` and `costUsd` from a committed price table (`MODEL_PRICING_JSON`
  override) — unknown prices stay `null`, never a fabricated $0.
  `RUN_BUDGET_USD` is a hard pre-call gate terminating with
  `budget_limit`.
- **Token streaming** — `answer_delta` SSE frames stream the answer
  live; `final` stays authoritative.
- **Tracing** — LangSmith when `LANGCHAIN_TRACING_V2` + key are set; absent
  keys keep everything local.

## 🔒 Security posture

`SECURITY.md` is the source of truth; the headline controls:

- Every path resolves against `SANDBOX_ROOT` — `..`, absolute, drive/UNC,
  NUL, symlink/junction escapes all rejected on any OS.
- `db_query` is read-only **twice** — statement guard (NFKC-normalized,
  comment-smuggling resistant) and a `mode=ro` connection.
- Rate limiting: sliding window per tool + per session (session = 4×);
  `RATE_LIMIT_BACKEND=redis` + `REDIS_URL` shares counters across server
  processes (opt-in, `pip install "mcp-toolkit[redis]"`).
- Tool output is untrusted data — sanitized, size-capped, never interpolated
  into system prompts (indirect-injection matrix is 13/13 in the dataset).
- The console is local, single-user, unauthenticated — registry mutations
  additionally need `ENABLE_PROMPT_ADMIN=1`.
- CI: `permissions: contents: read`; `pip-audit`, `gitleaks` full-history.
  Live evaluations stay manual and local — no scheduled job spends credit.

## 🚀 Quick Start

```bash
git clone <repo> && cd mcp-agent-toolkit-py
uv sync                                    # locked environment
uv run python -m scripts.seed_db           # data/demo.db fixture
cp .env.example .env                       # optional — works offline without it

# Try it (no keys needed — StubLLM + canned search):
uv run python -m scripts.demo              # scripted end-to-end demo
uv run mcp-toolkit-web                     # console at localhost:3000

# Live mode: set OPENAI_API_KEY (+ SEARCH_API_KEY for Tavily) in .env
```

**Connect it to Claude Desktop** — the server is a standard stdio MCP
server, so any MCP client can drive it:

```json
{
  "mcpServers": {
    "mcp-agent-toolkit": {
      "command": "uv",
      "args": ["run", "--project", "C:/path/to/mcp-agent-toolkit-py", "mcp-toolkit-server"],
      "env": {
        "SANDBOX_ROOT": "C:/path/to/mcp-agent-toolkit-py/data/sandbox",
        "DB_PATH": "C:/path/to/mcp-agent-toolkit-py/data/demo.db",
        "SEARCH_PROVIDER": "none"
      }
    }
  }
}
```

**Docker:**

```bash
docker build -t mcp-agent-toolkit .
docker run --rm mcp-agent-toolkit                          # stdio MCP server, UID 10001
docker run --rm -e WEB_HOST=0.0.0.0 -p 127.0.0.1:3000:3000 \
  mcp-agent-toolkit python -m mcp_toolkit.web              # console at localhost:3000
```

The image ships the built console, `evaluation/` (scorecard + A/B gate)
and the prompt registry; the web lifespan seeds the fixture DB into
`/data` on first run. `docker compose up web` is the same thing wired
for you — the published port stays loopback-only on the host.

## ⚙️ Environment Variables

`.env.example` is the reference. Headliners:

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | — | Live LLM; absent ⇒ deterministic `StubLLM` |
| `CHAT_MODEL_NAME` | `gpt-4o-mini` | OpenAI model |
| `JUDGE_MODEL_NAME` | = agent model | LLM-as-judge model — set a different one for an independent grader |
| `LLM_PROVIDER` | `auto` | `auto` · `openai` · `stub` |
| `SEARCH_PROVIDER` | `auto` | `auto` · `tavily` · `duckduckgo` · `none` |
| `SEARCH_API_KEY` | — | Tavily key |
| `SANDBOX_ROOT` | `./data/sandbox` | Only filesystem the tools may touch |
| `DB_PATH` | `./data/demo.db` | Read-only fixture DB |
| `RATE_LIMIT_PER_MINUTE` | `60` | Per tool; session bound is 4× |
| `RATE_LIMIT_BACKEND` | `memory` | `memory` · `redis` (needs `REDIS_URL` + extra) |
| `REDIS_URL` | — | Secret — may embed credentials |
| `MAX_TOOL_ITERATIONS` | `8` | Agent loop bound (≤ 50) |
| `MAX_TOOL_OUTPUT_CHARS` | `12000` | Tool-output cap reaching the model |
| `RUN_BUDGET_USD` | — | Hard per-run cost cap |
| `MCP_TRANSPORT` | `stdio` | `stdio` · `streamable-http` |
| `MCP_HTTP_TOKEN` | — | Required on non-loopback HTTP binds |
| `WEB_HOST` | `127.0.0.1` | Console bind; `0.0.0.0` inside containers |
| `WEB_PORT` | `3000` | Console port |
| `ENABLE_PROMPT_ADMIN` | off | Registry write endpoints |
| `LANGCHAIN_TRACING_V2` | `false` | LangSmith tracing opt-in |

## 🖥️ Frontend

React 19 + Vite + TypeScript strict + Tailwind — a deliberately thin client:

| Surface | Detail |
|---|---|
| Console | live tool-step cards, provisional streaming-answer card, markdown final answer with citations, telemetry footer |
| Multi-turn | prior turns sent as validated `history`; "New conversation" resets |
| Registry tab | versions, diffs, A/B progress over SSE, promote/rollback (admin-gated) |
| Export | per-run Markdown / JSON download — same hidden-field rules as the UI |
| States | designed empty / streaming / result / error + "Try again" recovery |
| A11y & QA | `aria-live` announcements, `prefers-reduced-motion`, ≥ 44 px mobile tap targets, CLS ≤ 0.01, no raw identifiers (automated sweep) |

| Empty state | Streaming | Guardrail block |
|---|---|---|
| ![Empty state with example questions](docs/ui-qa/state-empty.png) | ![Live tool-step cards while the agent works](docs/ui-qa/state-streaming.png) | ![A blocked call explained in plain language](docs/ui-qa/06-guardrail-block-1280.png) |

## 📁 Project Structure

```
src/mcp_toolkit/
├── config.py              # typed Settings (SecretStr, empty = absent)
├── client/                # MCP client, LLM adapters, agent loop, CLI, events
│   └── llm/               # OpenAILLM · StubLLM behind one protocol
├── server/                # MCPServer, tool handlers, guardrails, transports
│   └── guardrails/        # path_sandbox · sql_guard · sanitize · limiters
├── prompts/               # registry: loader · state machine · audit · A/B gate
├── observability/         # structlog · Prometheus · pricing table · request ctx
└── web/                   # FastAPI: SSE runs, registry API, static console
frontend/                  # React console (thin client)
evaluation/                # dataset · checks · judge · scorecard
prompts/                   # versioned YAML artifacts (agent_system, judge_rubric)
tests/                     # unit · integration (real stdio) · e2e (Playwright)
docs/                      # architecture · decisions · QA evidence · demo GIF
scripts/                   # demo · seed_db · prompts_cli · ui_qa capture
```

## 🧪 Verification

Every gate is reproducible locally and enforced in CI:

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict src
uv run pytest -q --cov=src --cov-branch --cov-fail-under=92   # 517 tests
uv run python -m evaluation.run_eval --offline                # harness smoke
uv run pytest tests/e2e -q                                    # 54 Playwright tests
npm --prefix frontend run lint && npm --prefix frontend run typecheck
npm --prefix frontend test -- --run && npm --prefix frontend run build
uv run pip-audit -r requirements.txt
gitleaks detect --no-banner
```

517 backend tests (92.98 % branch coverage) · 54 Playwright e2e · 55 Vitest —
all offline and deterministic; live tests are opt-in via `RUN_LIVE_TESTS=1`.

## 🛠️ Stack

Python ≥ 3.11 · `uv` · `mcp` SDK · OpenAI · FastAPI/uvicorn · pydantic v2 ·
sqlite3 (`mode=ro`) · structlog · prometheus-client · PyYAML · tenacity ·
pytest + hypothesis + respx + Playwright · React 19 + Vite + TS strict +
Tailwind · Docker (non-root)

## Known limitations & next steps

- The web console is a **local, single-user, unauthenticated** adapter —
  put an identity layer in front before exposing it.
- The default rate limiter is per-process; `RATE_LIMIT_BACKEND=redis` opts
  into shared counters (documented non-atomic check→record race — fine for
  a guardrail).
- Streamable HTTP is loopback + bearer-token, not Internet-hardened.
- The v1.2.0 scorecard passes, with two honest residuals: `ad-11` still
  misreports an oversize `write_file` as successful (one call now instead
  of eight — converged, but dishonest about the outcome), and `st-02`
  once fabricated MCP background details its tool output did not support.
- The dataset is 24 questions — a gate, not a benchmark; widening it is
  the natural next step.
- The committed scorecard is graded by `judgeModel = gpt-4o` — a
  different model than the agent's `gpt-4o-mini` (`JUDGE_MODEL_NAME`).
  Leave it unset and the judge falls back to the agent's model, which
  can favour its own style.
- `SEARCH_PROVIDER=duckduckgo` is keyless and can rate-limit under heavy
  use; Tavily is the stable live path.
- LangSmith tracing is opt-in and free-tier bounded: the monthly trace
  quota can be exhausted by repeated live runs, and retention is limited —
  the committed scorecard and `docs/eval/` captures are the durable
  evidence, not the platform.

## 💼 Need this for your own project?

The pieces are independently reusable: the guarded-invocation pipeline, the
stdio client + bounded agent loop, the Prompt Registry lifecycle, and the
EDD harness all work without the console. `docs/architecture.md` maps the
surfaces; `docs/DECISIONS.md` explains the non-obvious choices.

## 📄 License

MIT — © 2026 Kaarsten Anderson (<contact@kaarstenanderson.com>). See [LICENSE](LICENSE).
