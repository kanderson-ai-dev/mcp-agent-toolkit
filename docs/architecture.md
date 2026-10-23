# Architecture

A production-shaped **Model Context Protocol (MCP) server + agent client**
in Python. The server exposes four guarded tools over stdio; the client is
an autonomous agent that discovers them at runtime and drives an
OpenAI-compatible tool-calling loop — tool calling over an open protocol,
not framework bindings.

## Component view

```mermaid
flowchart LR
    subgraph client["client process"]
        CLI["agent CLI / demo"] --> LOOP["agent loop<br/>(bounded: MAX_TOOL_ITERATIONS)"]
        LOOP --> LLM["LLM adapter<br/>OpenAI · StubLLM"]
        LOOP --> MCPC["McpAgentClient<br/>(ClientSession wrapper)"]
        LOOP -.-> EV["event stream<br/>(AgentEvent, camelCase)"]
        LOOP -.-> TEL["telemetry<br/>tokens · cost · latency<br/>RUN_BUDGET_USD gate"]
    end

    MCPC -- "stdio · JSON-RPC<br/>params._meta.requestId" --> SRV

    subgraph server["server process (spawned by client)"]
        SRV["MCPServer<br/>(mcp SDK v2)"] --> INV["guarded_invoke"]
        INV --> RL["rate limiter<br/>(tool + session)"]
        RL --> HAND["tool handler"]
        HAND --> ENV["result envelope<br/>{status,data} | {status,code,message}"]
        ENV --> SAN["output sanitizer<br/>(control chars + size cap)"]
    end

    HAND --> WS["web_search<br/>tavily · duckduckgo · stub"]
    HAND --> DBQ["db_query<br/>sql_guard + read-only SQLite"]
    HAND --> RF["read_file"] & WF["write_file"]
    RF & WF --> SB["path sandbox<br/>(lexical + canonical)"]
    SB --> FS[("sandbox fs")]
    DBQ --> DB[("demo.db<br/>fixture reports")]
    WS --> NET(["search provider"])

    INV -.-> LOGS["structured JSON logs<br/>stderr · request_id · arg shape"]
    INV -.-> PROM["Prometheus registry<br/>/metrics + /healthz (opt-in)"]
    LOOP -.-> PROM
```

## Surfaces

| Surface | Module | Responsibility | Trust boundary |
|---|---|---|---|
| MCP transport | `server/app.py`, `server/__main__.py`, `server/streamable.py` | stdio server (canonical), opt-in Streamable HTTP | stdout = protocol frames only; logs → stderr; HTTP is loopback+token-gated |
| Invocation pipeline | `server/invocation.py` | rate limit → handler → envelope → sanitize → log + metrics | every call, no exceptions across the wire |
| Tools | `server/tools/` | `web_search`, `db_query`, `read_file`, `write_file` | tool output is untrusted data (LLM01) |
| Guardrails | `server/guardrails/` | path sandbox, read-only SQL, sanitize, rate limit | reject → typed error envelope, counted |
| Client transport | `client/mcp_client.py`, `client/server_spawn.py` | spawn server, env allow-list, `tools/list` → OpenAI defs | `OPENAI_API_KEY` never reaches the server env |
| Agent loop | `client/agent_loop.py` | LLM ⇄ tools, `max_iterations` bound, malformed-call recovery, `RUN_BUDGET_USD` cost gate | tool failures fed back as messages |
| Events | `client/events.py` | `AgentEvent` union, camelCase wire keys, `final` carries `usage`/`costUsd`/`durationMs` | parity with the oracle SSE fixture |
| Config | `config.py` | typed `Settings`, empty string = absent | secrets are `SecretStr`, redacted in logs |
| Observability | `observability/` | structlog + contextvars request IDs, Prometheus, committed model price table | dedicated registry per component |
| Entry points | `cli.py`, `scripts/demo.py`, `server/__main__.py` | `mcp-toolkit-agent`, demo, `mcp-toolkit-server` | CLI reconfigures streams to UTF-8 |

## Key flows

**Tool call:** client generates a `request_id` per call → sent as
`params._meta.requestId` → server extracts it in `guarded_invoke`, binds
it to the log context, and emits one `tool_invocation` record with the
same ID — the correlation chain visible in the client log, the event
stream, and the server log.

**Guardrail block:** a guardrail raises `ToolError(code, guardrail=…)` →
envelope `{status:"error",code}` with `isError=true` →
`mcp_guardrail_blocks_total{guardrail}` increments → the model sees the
typed error and can adapt.

**Termination:** the loop ends on a tool-call-free LLM response
(`terminated_by="answer"`), at `MAX_TOOL_ITERATIONS`
(`"iteration_limit"`) or when accumulated LLM cost reaches
`RUN_BUDGET_USD` (`"budget_limit"`) — guaranteed termination, recorded
in `agent_runs_total`.

**Telemetry:** every `final` event reports `durationMs`, provider
`usage` (prompt/completion/total tokens) and `costUsd` — cost is only
reported when the model has a price (committed table in
`observability/pricing.py`, overridable via `MODEL_PRICING_JSON`),
never estimated as $0. Scorecards and A/B evidence roll these up as
`avgTotalTokens`/`avgCostUsd`/`avgDurationMs` — informational deltas
that never gate promotion.

**MCP surface beyond `tools/call`:** every tool publishes
`ToolAnnotations` (read-only, destructive, idempotent, open-world) and a
tool-specific `outputSchema`; results carry the sanitized envelope both
as JSON text (back-compat) and as validated `structuredContent`. Two
read-only resources expose server state (`reports://schema`,
`sandbox://index`) and one prompt (`research_briefing`) composes the
tools into a cited-briefing user message — its `topic` argument lands in
a user message only, and the suggested filename is slugged so an
adversarial topic cannot smuggle a path. `tools/list` metadata flows
through `McpAgentClient.DiscoveredTool` into `GET /api/tools`, where the
console renders annotations as human chips via `lib/labels.ts` (raw hint
keys never render).

**Prompt flow:** the registry (`prompts/<name>/vN.yaml`) is the source of
truth → `prompts/loader.py` resolves `latest-promoted` (or an explicit
version) → the agent loop runs the resolved template → every run reports
which `{name, version, sha256}` produced it (`promptVersion` on `final`)
→ the same ref reaches the console chip, the metrics labels
(`agent_runs_total{prompt_version}`) and the A/B scorecards. The web
console resolves the prompt **per run** through `prompt_provider`, so a
promotion hot-swaps for new runs while in-flight runs keep theirs.
Mutations (candidate → A/B → promote / rollback) write hash-chained
entries to `data/prompt-registry/audit.jsonl`; `audit.verify_chain()`
rejects tampered history.

**Event flow (token streaming):** `LLMClient.complete` accepts an
optional synchronous `on_delta` — the loop re-emits each chunk as an
`answer_delta` event (ADR-8). `StubLLM` emits deterministic word-group
chunks; `OpenAILLM` maps `stream=True` content deltas and reassembles
fragmented `tool_calls` by index. Deltas are additive: the `final`
event still carries the authoritative full text, so the eval harness
and any consumer that predates the event type need no change.

## Transport matrix

| | stdio (default) | Streamable HTTP (opt-in) |
|---|---|---|
| Config | `MCP_TRANSPORT=stdio` | `MCP_TRANSPORT=streamable-http` + `MCP_HTTP_HOST/PORT/TOKEN` |
| Process model | client spawns the server; env allow-listed | standalone `mcp-toolkit-server` (or any ASGI host) |
| Listener | none — JSON-RPC on the child's stdout | uvicorn bind, `127.0.0.1` default |
| Auth | OS process boundary | bearer token — **mandatory on non-loopback**, constant-time compare |
| Secrets reaching the server | only allow-listed env vars | same + the token never logged |
| Intended use | the canonical transport (CLI, web adapter, tests) | clients that cannot spawn a subprocess; not Internet-hardened |
| Tests | `tests/integration/test_mcp_stdio.py` | `tests/integration/test_streamable_http.py` |
