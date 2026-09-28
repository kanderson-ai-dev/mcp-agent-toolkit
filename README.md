<div align="center">

# 🔌 MCP Agent Toolkit

**A production-shaped Model Context Protocol server + autonomous agent client — an LLM discovers four guarded tools over the open standard at runtime, recovers from live tool errors, and produces a cited report, with every invocation logged, rate-limited, and correlated by request ID.**

[![Node 20+](https://img.shields.io/badge/Node-20%2B-339933?logo=node.js&logoColor=white)](https://nodejs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.x%20strict-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![MCP](https://img.shields.io/badge/MCP-official%20SDK%20%C2%B7%20stdio-7C3AED)](https://modelcontextprotocol.io/)
[![CI](https://github.com/kanderson-ai-dev/mcp-agent-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/kanderson-ai-dev/mcp-agent-toolkit/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-98%25-brightgreen)](https://github.com/kanderson-ai-dev/mcp-agent-toolkit/actions/workflows/ci.yml)
[![Lint](https://img.shields.io/badge/lint-eslint%2010-red)](https://eslint.org/)
[![Type checked: tsc](https://img.shields.io/badge/type%20checked-tsc--strict-blue)](https://www.typescriptlang.org/tsconfig#strict)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

```text
$ npm run agent -- "Which security and observability reports exist in the
  internal database? Search the web for MCP security best practices,
  then save a cited summary to reports/demo-briefing.md"

[iter 0] llm: connected — 4 tools discovered
         (web_search, db_query, read_file, write_file), llm=openai
[iter 1] → tool_call db_query  {"query":"SELECT title, author FROM reports WHERE category IN (...)"}
[iter 1] ← ERROR db_query (9ms)  "no such column: category"     ← real error, agent recovers
[iter 1] → tool_call web_search {"query":"MCP security best practices","max_results":5}
[iter 1] ← ok web_search (1224ms) provider=duckduckgo
[iter 2] → tool_call db_query  {"query":"SELECT title, author FROM reports"}
[iter 2] ← ok db_query (1ms)
[iter 3] → tool_call write_file {"path":"reports/demo-briefing.md", ...}
[iter 3] ← ok write_file (4ms)

=== FINAL (4 iterations, 4 tool calls, answer) ===
```

*Real run — full transcript in [`docs/demo-transcript.txt`](docs/demo-transcript.txt), the written report in `data/sandbox/reports/`.*

</div>

---

## 📌 What this is

A **standalone MCP server + agent client** in strict TypeScript. The server
exposes four tools over the official [`@modelcontextprotocol/sdk`](https://github.com/modelcontextprotocol/typescript-sdk)
with stdio transport: `web_search`, `db_query` (read-only SQLite),
`read_file` and `write_file` (sandboxed). The client spawns the server as a
child process and speaks plain MCP — `tools/list`, `tools/call`,
`params._meta` correlation — exactly like Claude Desktop or any other
MCP-capable host would. Nothing is imported in-process; the protocol is the
boundary.

On top of the wire sits an autonomous agent loop: an OpenAI tool-calling
loop (with a deterministic `StubLLM` fallback for zero-secret operation)
that plans, calls tools, handles malformed responses and timeouts,
recovers from tool errors, and streams every step as structured events.

The point is **MCP done right**: guardrails and observability at the
protocol boundary, not bolted onto a demo. Every tool call is
schema-validated, rate-limited, sanitized on the way back (the web is
untrusted — OWASP LLM01), logged as JSON with a correlated request ID, and
counted in Prometheus metrics.

---

## 💡 Why this matters — for your project, or for a technical reviewer

- 🔌 **The protocol is the real boundary.** The agent discovers tools via
  `tools/list` and invokes them via `tools/call` over JSON-RPC stdio — no
  shared imports, no framework glue. Swap the client for any MCP host and
  the server just works.
- 🛡️ **Defense in depth, not a single regex.** `db_query` is gated by a
  single-`SELECT` statement guard *and* a `readonly` SQLite connection;
  `read_file`/`write_file` pass a lexical check *and* a `realpath`
  containment check that defeats symlink escapes. Each layer fails closed
  on its own.
- 🧼 **Untrusted output never becomes instructions.** Every tool result is
  sanitized server-side (control/bidi/zero-width chars stripped, injection
  patterns neutralized, `MAX_TOOL_OUTPUT_CHARS` cap) before it reaches the
  model context — the OWASP LLM01 control for indirect prompt injection.
- 🔑 **The server never sees the LLM key.** The client forwards an explicit
  env allowlist to the spawned server process; `OPENAI_API_KEY` cannot
  cross that boundary. Empty secrets count as absent, so CI runs with zero
  credentials.
- 🧾 **Request-ID correlation end-to-end.** The client stamps a UUID into
  `params._meta.requestId` per call; the server logs it on every
  invocation line — client event stream joins server logs trivially.
- 🚦 **Every failure is a first-class signal.** Timeouts, malformed
  responses, unknown tools and guardrail rejections normalize to typed
  error envelopes — the agent sees a clean `failure` reason, not a stack
  trace (the demo transcript shows a live SQL error recovered).
- 🧪 **The transport is real in tests too.** The integration suite spawns
  the actual server over stdio and asserts request-ID correlation and
  rate-limit enforcement across the wire — not a mock of MCP.
- 📉 **CI green with zero secrets.** Fork, clone, `npm ci && npm test` —
  110 tests pass offline; the deterministic `StubLLM` and
  `SEARCH_PROVIDER=none` need no keys.

---

## 🏗️ Architecture

```mermaid
graph LR
    subgraph client["Agent client"]
        CLI[CLI entry] --> LOOP[Agent loop<br/>OpenAI tool calling]
        LOOP --> MC[MCP client<br/>StdioClientTransport]
        LOOP -.->|no OPENAI_API_KEY| STUB[StubLLM<br/>deterministic]
        LOOP -->|chat.completions| OAI[(OpenAI API)]
    end

    MC ==>|"JSON-RPC over stdio<br/>tools/list · tools/call<br/>requestId via params._meta"| MS

    subgraph server["MCP server (child process)"]
        MS[McpServer<br/>StdioServerTransport] --> REG[Tool registry<br/>+ guardrail wrapper]
        REG --> RL[Rate limiter<br/>token bucket]
        RL --> T1[web_search]
        RL --> T2[db_query]
        RL --> T3[read_file]
        RL --> T4[write_file]
        T1 --> SANE[Output sanitizer]
        T2 --> SQLG[SELECT-only guard<br/>+ readonly conn]
        T3 --> FS[Path sandbox]
        T4 --> FS
        REG --> LOG[pino JSON → stderr]
        REG --> MET[Prometheus metrics<br/>optional :METRICS_PORT]
    end

    T1 -->|HTTPS| EXT1[(Tavily /<br/>DuckDuckGo)]
    T2 --> EXT2[(SQLite<br/>readonly)]
    T3 --> EXT3[(data/sandbox/)]
    T4 --> EXT3
```

Full rationale and the tool/guardrail matrix:
[`docs/architecture.md`](docs/architecture.md).

---

## 📊 Evaluation-Driven Development (EDD)

The quality gates are binary and enforced in CI — a regression fails the
build rather than being shrugged off:

| Dimension | Metric | Threshold | Latest run |
|---|---|---|---|
| Test suite | unit + security + real-stdio integration | 100% green | 110/110 ✅ |
| Coverage | lines / branches / functions / statements | ≥ 85% each | 98.5 / 85.1 / 100 / 97.5 ✅ |
| Type safety | `tsc --strict` | 0 errors | ✅ |
| Lint | `eslint` strict (typescript-eslint) | 0 warnings | ✅ |
| Security suite | traversal · write SQL · injection · rate-limit rejections | all must **fail loudly** | ✅ |
| Dependency audit | `npm audit --omit=dev` | 0 high/critical | ✅ |

Reproduce: `npm run test:cov` (coverage gate), `npm run verify`-equivalent
sequence in [`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## 🔌 API

The server speaks standard MCP (JSON-RPC 2.0 over stdio): `initialize`,
`tools/list`, `tools/call`. Tools:

| Tool | Input | Guardrails | Returns |
|---|---|---|---|
| `web_search` | `query` (1–400 chars), `max_results` (default 5) | provider allowlist, timeout, output sanitizer + size cap | `{ provider, results: [{title, url, snippet}] }` |
| `db_query` | `query` — a single `SELECT`/`WITH` | statement guard **+** `readonly` connection, 500-row cap | `{ rows, row_count, truncated }` |
| `read_file` | `path` relative to sandbox | lexical + `realpath` containment, `READ_FILE_MAX_BYTES` cap | `{ path, content }` |
| `write_file` | `path`, `content` | same containment, `WRITE_FILE_MAX_BYTES` cap | `{ path, bytes_written }` |

Example `tools/call` payload (real shape — the client adds
`_meta.requestId` automatically):

```json
{
  "jsonrpc": "2.0", "id": 7, "method": "tools/call",
  "params": {
    "name": "db_query",
    "arguments": { "query": "SELECT title, author FROM reports ORDER BY published_at DESC LIMIT 5" },
    "_meta": { "requestId": "de30734e-8f77-4418-8a27-65557d8ade40" }
  }
}
```

Agent CLI:

```bash
npm run agent -- "question"          # live OpenAI loop (needs OPENAI_API_KEY)
npm run agent -- "question" --stub   # deterministic, zero secrets
npm run agent -- "question" --json   # machine-readable event stream
npm run demo                         # seeded E2E run → docs/demo-transcript.txt
```

---

## 📡 Observability & cost

- **pino JSON logs on stderr** (stdout is reserved for the MCP transport):
  every tool invocation logs tool name, sanitized input args, output
  preview, duration, ok/error, `session_id` and `request_id`.
- **Request-ID correlation**: the client stamps `params._meta.requestId`;
  the server echoes it on every log line — join client events to server
  logs by UUID.
- **Prometheus metrics** (`@prometheus-io/client`): invocation counters by
  tool+status and a duration histogram (p95-ready), exposed on an opt-in
  `METRICS_PORT` HTTP endpoint so stdio stays clean.
- **Cost**: the only metered dependency is the LLM. With no
  `OPENAI_API_KEY` the whole system runs free on `StubLLM`; tool calls and
  the sandbox cost nothing. `MAX_TOOL_ITERATIONS` bounds worst-case token
  spend per run.

Real server log line from the recorded demo:

```json
{"level":30,"service":"mcp-server","event":"tool_invocation","tool":"db_query",
 "request_id":"de30734e-8f77-4418-8a27-65557d8ade40","session_id":"3bb996d6-…",
 "duration_ms":0,"ok":true,"input":"{\"query\":\"SELECT title, author FROM reports\"}",
 "output":"[{\"type\":\"text\",\"text\":\"{\\\"status\\\": \\\"ok\\\"…"}
```

---

## 🔒 Security posture

Sandboxed filesystem (lexical + `realpath` containment), read-only SQL
(statement guard **and** `readonly` connection), output sanitization
against indirect prompt injection (OWASP LLM01), per-tool/session rate
limiting, env allowlist so the server never sees `OPENAI_API_KEY`,
secret-redacted logs, `gitleaks` + `npm audit` in CI on `pull_request`
with `contents: read`. Full threat model and mechanism table:
[`SECURITY.md`](SECURITY.md).

---

## 🚀 Quick Start

```bash
npm ci
cp .env.example .env        # optional — paste OPENAI_API_KEY for the live loop
npm run seed:db             # fixture DB for db_query (8 reports)
npm run demo                # recorded E2E: seeds + runs, saves transcript
npm run agent -- "your question"
```

Degrades cleanly with zero secrets: no `OPENAI_API_KEY` → deterministic
`StubLLM`; no `SEARCH_API_KEY` → keyless DuckDuckGo; `SEARCH_PROVIDER=none`
→ fully offline. The entire CI suite runs with no credentials.

```bash
docker compose up                    # runs the default demo question once
docker compose run --rm agent "…"    # custom question
docker compose run --rm agent --stub "offline run, no keys"
```

The image is multi-stage (non-root `node` user), seeds the fixture DB at
build time, and mounts only `data/sandbox/` as a volume — the single
writable surface. `.env` is read at runtime via `env_file`; nothing is
baked in.

## ⚙️ Environment Variables

| Variable | Required | Secret? | Purpose |
|---|---|---|---|
| `OPENAI_API_KEY` | no | ✅ | Live LLM for the agent loop; `StubLLM` when absent |
| `CHAT_MODEL_NAME` | no | — | Model override (default `gpt-4o-mini`) |
| `LLM_PROVIDER` | no | — | `auto` \| `openai` \| `stub` |
| `SEARCH_PROVIDER` | no | — | `auto` \| `tavily` \| `duckduckgo` \| `none` |
| `SEARCH_API_KEY` | no | ✅ | Tavily key (optional paid provider) |
| `MCP_SERVER_COMMAND` / `MCP_SERVER_ARGS` | no | — | Override how the client spawns the server |
| `SANDBOX_ROOT` | no | — | The only filesystem root tools may touch |
| `DB_PATH` | no | — | SQLite fixture path (`npm run seed:db`) |
| `RATE_LIMIT_PER_MINUTE` | no | — | Per tool, per session (default 60) |
| `MAX_TOOL_ITERATIONS` | no | — | Agent loop bound — must terminate |
| `MAX_TOOL_OUTPUT_CHARS` | no | — | Tool output truncation cap (untrusted data) |
| `READ_FILE_MAX_BYTES` / `WRITE_FILE_MAX_BYTES` | no | — | FS size caps |
| `LOG_LEVEL` | no | — | pino level; logs go to stderr |
| `METRICS_PORT` | no | — | Empty = disabled; e.g. `9108` → `/metrics` HTTP |
| `OUTPUT_FORMAT` | no | — | `pretty` \| `json` agent event stream |

Never commit secrets. See [`.env.example`](.env.example) for the full,
commented template.

## 📁 Project Structure

```
src/
├── server/            # MCP server (stdio) — separate process
│   ├── server.ts      #   tool registration + guardrail wrapper + logging
│   ├── tools/         #   web_search, db_query, read_file, write_file
│   ├── guardrails/    #   path sandbox, SQL guard, rate limiter, sanitizer
│   └── observability/ #   pino logger, Prometheus metrics, request ctx
├── client/            # agent CLI — spawns the server, speaks MCP
│   ├── mcp-client.ts  #   tools/list + tools/call + env allowlist
│   ├── agent-loop.ts  #   tool-calling loop (bounded, must terminate)
│   ├── events.ts      #   structured event stream (pretty|json)
│   └── llm/           #   OpenAI impl + deterministic StubLLM
└── shared/            # config (env > .env, empty secrets = absent),
                       # ToolError + result envelopes
scripts/               # seed-db.ts, demo.ts (records the transcript)
tests/                 # 110 tests — unit, security, real-stdio integration
docs/                  # architecture.md, demo-transcript.txt (real run)
data/sandbox/          # the ONLY writable surface for the fs tools
.github/workflows/     # CI: lint → typecheck → tests+coverage → build → audit
```

## 🧪 Verification

```bash
npm run lint           # eslint — 0 warnings
npm run typecheck      # tsc --noEmit strict — 0 errors
npm run test:cov       # 110 tests offline; ≥85% all metrics (actual ~98.5% lines)
npm run build          # compile to dist/
npm audit --omit=dev   # 0 vulnerabilities
```

CI (`.github/workflows/ci.yml`): lint → typecheck → tests+coverage → build
→ `npm audit` + `gitleaks`, on `pull_request`, Node 20/22/24 matrix,
`permissions: contents: read`. A fork passes with zero secrets.

## 🛠️ Stack

Node 20+ · TypeScript 5 strict ESM · `@modelcontextprotocol/sdk` (stdio
transport, `tools/list`/`tools/call`, `_meta` correlation) · OpenAI
tool-calling (`chat.completions`) · `better-sqlite3` (readonly fixture DB)
· `zod` (input schemas) · `pino` (JSON logs) · `@prometheus-io/client`
(metrics) · Vitest + `@vitest/coverage-v8` · ESLint 10 +
typescript-eslint · Docker multi-stage.

## Known limitations & next steps

- **stdio only.** The transport trusts the spawning client — the correct
  MCP model for a local server. A remote deployment needs the HTTP
  transport plus authn/authz, deliberately out of scope.
- **In-process rate limiting.** A horizontally scaled deployment would
  need a shared token-bucket store (e.g. Redis).
- **Keyless search quality.** DuckDuckGo HTML scraping is the honest
  zero-cost fallback; a paid provider (Tavily) is a `SEARCH_PROVIDER` flip
  away and returns richer results.
- **No streaming token UX.** The event stream is structured JSON/pretty
  lines; an SSE surface for a browser console is a natural next step
  (`AgentEvent` union is already shaped for it).
- **SQLite-centric SQL guard.** `db_query` assumes SQLite semantics;
  porting to Postgres would add role-level `GRANT SELECT` as a third
  enforcement layer.

---

## 💼 Need this for your own project?

**I build production-shaped agentic AI systems — MCP servers and clients,
guardrail-first tool surfaces, cost-aware agent loops, and the
observability discipline to actually trust them in production.**

If you need an internal capability exposed safely to agents over an open
protocol — with sandboxing, injection defenses, audit-grade logging and a
test suite that proves the guardrails fail loudly — this codebase is the
working template.

This project is the protocol layer underneath the Agentic AI portfolio
ladder (`agentic-api` → `agentic-rag-system` → `agentic-web-researcher` →
`langgraph-multiagent-orchestrator`): the open standard those agents
consume tools through, implemented end-to-end.

## 📄 License

[MIT](LICENSE)
