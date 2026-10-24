# mcp-agent-toolkit

> A production-shaped **Model Context Protocol system** in Python: a real MCP
> server exposing four guarded tools, an agent client that discovers and calls
> them over stdio, a versioned Prompt Registry with a measured A/B gate, and a
> web console that streams every step live. Quality is measured, not claimed —
> dataset, deterministic checks, and a separately-prompted LLM-as-judge scorecard.

[![Python](https://img.shields.io/badge/python-≥3.11-blue)](pyproject.toml)
[![MCP](https://img.shields.io/badge/MCP%20SDK-≥2.3-purple)](pyproject.toml)
[![CI](https://img.shields.io/badge/CI-ruff%20·%20mypy%20--strict%20·%20pytest%20·%20e2e-green)](#-verification)
[![Coverage](https://img.shields.io/badge/branch%20coverage-92%25%20enforced-brightgreen)](#-verification)
[![License](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

![Web console demo](docs/web-demo.gif)

## 📌 What this is

`mcp-agent-toolkit` demonstrates the Model Context Protocol doing real work
inside an agentic system — not a thin wrapper around an LLM:

- An **MCP server** (official Python SDK) exposes exactly four tools —
  `web_search`, `db_query`, `read_file`, `write_file` — each behind
  enforceable guardrails: a lexical + canonical path sandbox, read-only SQL
  (statement guard *and* `mode=ro` connection), sliding-window rate limits,
  and output sanitization.
- An **agent client** spawns that server over canonical **stdio** transport,
  discovers the tools at runtime via `tools/list`, and runs a plain
  OpenAI tool-calling loop. No LangChain/LangGraph — the point of MCP is that
  it is framework-independent.
- A **FastAPI web adapter** streams runs over SSE; a **React console**
  renders every tool call, result and token live.
- A **Prompt Registry** versions the system prompt and judge rubric as YAML
  artifacts; promotion is gated by a real A/B evaluation and every mutation
  lands in a hash-chained audit log.
- An **evaluation harness** turns quality into a gate: a 24-question dataset
  (13 adversarial), deterministic checks, and a judge with its own rubric
  prompt produce a scorecard a prompt must beat to promote.

## 💡 Why this matters — for your project, or for a technical reviewer

Everything below is verifiable in this repository — no aspirational claims:

- **Runtime tool discovery** — the client never hardcodes tool schemas;
  `tools/list` output becomes OpenAI tool definitions. Drop a tool from the
  server and the agent adapts on the next run.
- **Guardrails that fail loudly** — every rejection is a typed envelope
  (`forbidden`, `rate_limited`, `too_large`…), counted in metrics and fed
  back to the model so it can adapt honestly. Path escapes, multi-statement
  SQL and prompt-injection attempts all have dedicated failing tests.
- **Measured quality** — a real scorecard (`evaluation/scorecard.json`,
  live run, dataset v1.2.0): task success **4.63/5**, grounding **4.67/5**,
  tool usage **4.42/5**, adversarial resistance **13/13**, appropriate
  tool use **100 %** — gate passing.
- **Prompt lifecycle as an engineered surface** — real promotions happened:
  `agent_system` v3 beat v1 on a **live** A/B run, and when the expanded
  24-question dataset later failed the grounding gate, the fix went through
  the same machinery — `judge_rubric` v2 + `agent_system` v4, the latter
  promoted only after winning its own live A/B against v3. The audit chain
  records every step.
- **Production posture** — `mypy --strict` clean, `ruff` enforced, branch
  coverage gated at 92 %, `pip-audit` + `gitleaks` clean over full history,
  Docker image runs as UID 10001, zero secrets in the tree.
- **Offline-first honesty** — the entire suite runs with zero credentials
  (`StubLLM` + canned search). Live paths are opt-in; no score, trace or
  screenshot is ever fabricated.

## 🏗️ Architecture

```mermaid
flowchart LR
    subgraph client["client process"]
        CLI["mcp-toolkit-agent CLI"] --> LOOP["agent loop<br/>(bounded iterations + budget gate)"]
        LOOP --> LLM["LLM adapter<br/>OpenAI · StubLLM"]
        LOOP --> MCPC["McpAgentClient"]
        LOOP -.-> EV["AgentEvent stream<br/>(camelCase JSON)"]
        PROMPT["Prompt Registry<br/>(promoted YAML)"] --> LOOP
    end

    MCPC -- "stdio · JSON-RPC" --> SRV

    subgraph server["server process"]
        SRV["MCPServer"] --> INV["guarded_invoke"]
        INV --> RL["ToolLimiter<br/>(memory · Redis opt-in)"]
        RL --> TOOLS["4 tool handlers"]
        TOOLS --> ENV["result envelope + sanitizer"]
    end

    TOOLS --> WS["web_search"] --> NET(["Tavily · DuckDuckGo · stub"])
    TOOLS --> DBQ["db_query"] --> DB[("demo.db<br/>read-only")]
    TOOLS --> FS["read_file · write_file"] --> SB["path sandbox"] --> FSYS[("sandbox fs")]

    subgraph web["web console"]
        API["FastAPI<br/>/api/chat + SSE"] --> UI["React + Vite + Tailwind"]
        EV -.-> API
    end
```

| Surface | Module | What it does |
|---|---|---|
| MCP server | `src/mcp_toolkit/server/` | `MCPServer`, stdio canonical, Streamable HTTP opt-in, annotations + `outputSchema` + resources + prompts |
| Guardrails | `server/guardrails/` | path sandbox, SQL guard, sanitizer, `ToolLimiter` (in-memory; Redis behind `RATE_LIMIT_BACKEND=redis`) |
| Agent loop | `client/agent_loop.py` | LLM ⇄ tools, `MAX_TOOL_ITERATIONS` + `RUN_BUDGET_USD` bounds, delta streaming via `on_delta` |
| MCP client | `client/mcp_client.py` | spawns the server, env allow-list, `tools/list` → OpenAI defs, request-id correlation |
| Web adapter | `web/` | `POST /api/chat` → `201 {runId}` → `GET /api/chat/stream` (SSE), `/api/tools`, `/api/status`, registry API |
| Prompt Registry | `prompts/` + `src/mcp_toolkit/prompts/` | versioned YAML, candidate → A/B → promote/rollback, hash-chained audit |
| Evaluation | `evaluation/` | dataset, deterministic checks, judge, scorecard artifact |
| Console | `frontend/` | React 19 + Vite + TS strict + Tailwind — thin client, zero business logic |

## 📊 Evaluation-Driven Development (EDD)

Quality regresses like a failing test. `evaluation/run_eval.py` drives the
dataset through the real agent (live mode: real OpenAI + real MCP transport),
runs deterministic checks, then an LLM-as-judge — own rubric prompt, model
configurable via `JUDGE_MODEL_NAME` (the agent's model by default) — scores
each answer.

Latest committed scorecard (`evaluation/scorecard.json`, live,
`gpt-4o-mini`, dataset v1.2.0 — 24 questions, `agent_system@v4` +
`judge_rubric@v2`):

| Dimension | Result | Gate |
|---|---|---|
| Task success | **4.63 / 5** | ≥ 4.0 |
| Grounding / citation | **4.67 / 5** | ≥ 4.0 |
| Tool usage | **4.42 / 5** | — |
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

Remaining honest noise: `ad-11` now makes a single call instead of eight,
but the model still claims the write succeeded once instead of refusing
cleanly, and `st-02` fabricated MCP background details the tool output
did not support.

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

Prompts are versioned data, not code-embedded strings:

```mermaid
stateDiagram-v2
    [*] --> draft: artifact authored
    draft --> candidate: candidate submitted
    candidate --> promoted: live A/B gate passed + promote
    promoted --> rolled_back: a newer version promotes, or rollback
    rolled_back --> promoted: promote again (new A/B gate)
```

| CLI (`uv run python -m scripts.prompts_cli`) | API (`/api/prompts`, mutations need `ENABLE_PROMPT_ADMIN=1`) |
|---|---|
| `list`, `show`, `diff` | `GET /api/prompts`, `/{name}`, `/{name}/diff` |
| `candidate` | `POST /{name}/candidates` |
| `ab` (live) | `POST /{name}/ab-test` → `GET /ab/{run_id}/stream` (SSE) |
| `promote` | `POST /{name}/promote` (A/B-gated; `overrideReason` is audited) |
| `rollback` | `POST /{name}/rollback` |
| `audit` | `GET /api/prompts/audit` |

The story this repo actually contains: `agent_system` v3 was promoted over v1
after a **live** A/B run (judged, gate passed, 13/13 adversarial cases
resisted on both sides); v1's `rolled_back` status is the trail of the
rollback path being exercised for real. When the expanded v1.2.0 dataset
then failed the grounding gate on v3, the remediation ran through the
same lifecycle: `judge_rubric@v2` (promoted with an audited override) and
`agent_system@v4` (promoted only after beating v3 in its own live A/B).
Every mutation is a hash-chained `data/prompt-registry/audit.jsonl` entry —
`prompts_cli audit` / `verify_chain()` detects tampering.

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
- CI: `permissions: contents: read`, `pull_request` only; `live-eval.yml`
  never runs on PRs; `pip-audit`, `gitleaks` full-history, Dependabot.

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
uv run pytest -q --cov=src --cov-branch --cov-fail-under=92   # 492 tests
uv run python -m evaluation.run_eval --offline                # harness smoke
uv run pytest tests/e2e -q                                    # 54 Playwright tests
npm --prefix frontend run lint && npm --prefix frontend run typecheck
npm --prefix frontend test -- --run && npm --prefix frontend run build
uv run pip-audit -r requirements.txt
gitleaks detect --no-banner
```

492 backend tests (92.9 % branch coverage) · 54 Playwright e2e · 55 Vitest —
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
- The committed scorecard was produced with `judgeModel = gpt-4o-mini` —
  the same model as the agent, which can favour its own style. Set
  `JUDGE_MODEL_NAME` to a different model family for a grader that is
  model-independent, not just prompt-independent.
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
