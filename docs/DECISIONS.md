# Architecture Decision Records

Decisions taken during the Python port. Format: context → decision →
alternatives → consequences.

## ADR-1 — Rebuild the toolkit in Python

**Context.** The reference implementation is TypeScript
(`../mcp-agent-toolkit`, unpublished). Agent-engineer roles read the stack
as Python-first (LangGraph, OpenAI Agents SDK, Langfuse/LangSmith, evals);
the portfolio keeps exactly one TypeScript backend elsewhere.

**Decision.** Rebuild the backend, MCP server, MCP client, agent loop,
evaluation harness and tooling in Python. The TypeScript repo remains the
*behavioural oracle*: its tests are ported one-for-one, its dataset,
scorecard and SSE event stream are committed as fixtures under
`tests/fixtures/oracle/`, and the REST/SSE wire contract stays
byte-compatible so the copied React console works unchanged.

**Alternatives.** Keep TypeScript (rejected — contradicts the
Python-first portfolio strategy); rewrite the UI too (rejected — see
ADR-4).

**Consequences.** Two codebases share one wire contract, pinned by the
oracle fixtures and a contract test. Fixes for frontend drift go in the
backend, never the frontend.

## ADR-2 — `FastMCP` high-level API vs low-level `Server`

**Context.** The official `mcp` Python SDK offers a low-level `Server`
class (manual request handlers) and the high-level `FastMCP` API
(declarative `@mcp.tool()` registration, stdio runner built in).

**Decision.** `FastMCP`. The four tools are registered through one
`ToolDefinition` abstraction wrapped by a single middleware (request-id
binding, rate limit, timing, structured log, metrics, sanitization,
`ToolError` → error envelope with `isError=true`).

**Alternatives.** Low-level `Server`: more control over `_meta` access
and result shaping, but re-implements what `FastMCP` already does well
and drifts from the mainstream SDK usage reviewers expect.

**Consequences.** A Phase-3 spike test pins how `isError`, request
`_meta` and stdout behave in the installed SDK version; findings are
recorded here (see *Spike notes* below once landed). If `FastMCP`
cannot propagate `_meta` on `tools/call`, fall back to a raw
`CallToolRequest` on the client side and/or a low-level handler —
recorded in the spike.

## ADR-3 — No LangChain/LangGraph in the agent loop

**Context.** The portfolio's other projects already demonstrate
LangGraph. This project's point is that MCP is framework-independent:
tools are discovered at runtime and called over an open standard.

**Decision.** A plain OpenAI tool-calling loop (`AsyncOpenAI` +
`tools`), bounded by `MAX_TOOL_ITERATIONS`, with parallel tool calls in
one assistant turn executed sequentially for a deterministic transcript.

**Alternatives.** LangGraph `create_react_agent` / OpenAI Agents SDK —
both hide the protocol, which is the thing being demonstrated.

**Consequences.** The loop is ~100 lines, fully typed, and its event
stream is the single source for the CLI, the SSE adapter and the UI.

## ADR-4 — Reuse the React console instead of rewriting it

**Context.** The oracle's console already passed rigorous QA
(screenshots, E2E forbidden-literal scan, label dictionary).

**Decision.** Copy `frontend/` (React 19 + Vite + TS + Tailwind, Radix
primitives, `lucide-react`, `react-markdown`), then extend it. The
backend owns all logic; the frontend only renders events.

**Alternatives.** (b) HTMX + Jinja + Tailwind — simpler, but the
SSE/event-timeline UX is hard to match and job postings ask for React;
(c) Streamlit/Gradio — wrong portfolio signal.

**Consequences.** The wire contract must stay byte-compatible (ADR-5);
any drift is fixed in the backend. Frontend keeps its own build/test
toolchain (npm) — the only TypeScript in the repo.

## ADR-5 — camelCase JSON on the wire

**Context.** The copied frontend expects the oracle's event JSON —
camelCase field names (`requestId`, `durationMs`, `terminatedBy`,
`promptVersion`…).

**Decision.** Python models use `snake_case` internally; serialization
uses a Pydantic `to_camel` alias generator with
`populate_by_name=True`. A contract test pins the exact JSON of every
event type against `tests/fixtures/oracle/sse-events.stub.json`.

**Alternatives.** Serialize snake_case and patch the frontend (rejected
— violates "fix drift in the backend"); hand-written `alias=` per field
(rejected — error-prone boilerplate).

**Consequences.** Internal Python code stays idiomatic; the wire stays
compatible; new event fields are additive/optional.

## ADR-6 — HTML parser: `beautifulsoup4` + `lxml`

**Context.** The keyless DuckDuckGo fallback parses an HTML endpoint.
Candidates: `selectolax` (fast, modern) vs `beautifulsoup4` + `lxml`.

**Decision.** `beautifulsoup4` + `lxml`. The parsing is a handful of
CSS-selector queries over one result page — throughput is irrelevant at
this scale — and bs4's API is the more widely recognized, better
documented option for reviewers; `lxml` underneath gives libxml2-grade
robustness on messy HTML.

**Alternatives.** `selectolax` — faster, but no functional gain here and
one more unfamiliar API; `node-html-parser`-style hand parsing —
fragile.

**Consequences.** Both are pinned dependencies; `types-lxml` in dev for
`mypy --strict`. Only the DuckDuckGo provider touches the parser.

## ADR-7 — Synchronous `sqlite3` inside `asyncio.to_thread`

**Context.** `db_query` must be read-only twice (statement guard +
connection) without an ORM; the tool handlers are async.

**Decision.** stdlib `sqlite3` opened read-only (`file:…?mode=ro` URI,
`PRAGMA query_only=ON`), with blocking calls wrapped in
`asyncio.to_thread` inside the async handler.

**Alternatives.** `aiosqlite`/`sqlalchemy` — extra dependency and ORM
surface for a fixture DB; aiosqlite is itself a thread wrapper.

**Consequences.** Zero new dependencies, deterministic behaviour, and
the event loop never blocks on I/O.

---

## ADR-8 — Synchronous `on_delta` callback for token streaming

**Context.** Token-level streaming (`answer_delta` events) needs a
channel from inside the LLM call to the agent's event emitter.

**Decision.** `LLMClient.complete` takes an optional
`on_delta: Callable[[str], None]` — a *synchronous* callback. The
loop's `emit` is sync (in-memory append / `queue.put_nowait`), so an
async callback would only add `await` ceremony and an extra place to
fail. `final` still carries the full text — deltas are additive, and
consumers that predate them (eval harness, older frontends) are
unaffected.

**Alternatives.** `AsyncIterator` yield-per-chunk changes the call's
return contract; `Callable[[str], Awaitable[None]]` forces every
double to `async def` its no-op. Both cost more than the feature earns.

**Consequences.** StubLLM emits deterministic word-group chunks whose
concatenation is byte-identical to `content`; OpenAILLM maps
`stream=True` content deltas onto it and reassembles fragmented
`tool_calls` by index. Pretty-mode CLI suppresses deltas; `--json`
and the SSE layer forward them.

---

## ADR-9 — Opt-in Redis rate limiter behind `ToolLimiter`

**Context.** Several MCP server processes sharing one session budget
need shared counters; the in-memory limiter must stay the default.

**Decision.** `ToolLimiter` protocol (`check`/`record`); the Redis
backend stores `timestamp:uuid` members in sorted sets, trims the
60 s window per access, and expires idle keys. Plain sorted-set
commands, no Lua — check→record is two operations, accepting the
single-call race window a guardrail can tolerate. `redis` is an
optional extra; selecting the backend without the package or without
`REDIS_URL` fails loudly at startup.

**Alternatives.** Lua `eval` for atomicity — `fakeredis` cannot
emulate it, and a hard dependency on script support buys marginal
correctness for a bounded race. `redis.asyncio` — the loop-blocking
cost of one localhost `ZADD` is ~0.1 ms, cheaper than migrating the
whole limiter surface to async.

**Consequences.** `RATE_LIMIT_BACKEND=redis` + `REDIS_URL` (a secret)
opt in; `RATE_LIMIT_KEY_PREFIX` namespaces counters on a shared
instance. Memory backend unchanged and still the default.

---

## Spike notes (to be filled as phases land)

- *Phase 3.1 (`mcp` SDK):* pending — will record `isError` production,
  request `_meta` access from a tool handler, and stdout purity.

- *Phase 16 (`mcp` SDK 2.x, structured output):* `Annotated[CallToolResult,
  X]` publishes `X`'s schema as `outputSchema` **and** validates a manually
  returned `CallToolResult`'s `structuredContent` against `X` on every
  non-error call — error results skip validation entirely. Caveat learned
  by spike: annotating with `X | None` (any union/generic) makes the SDK
  wrap the schema in `{"result": X}` via a synthesized model instead of
  publishing `X` directly; the annotation must be the bare model. When a
  sanitized text envelope is truncated mid-JSON no faithful structure
  exists — `_result` degrades to a typed `too_large` error arm +
  `is_error=True` rather than shipping an unvalidatable result.

- *Phase 16 (`mcp` SDK 2.x, transports):* `streamable_http_client` yields
  a 2-tuple `TransportStreams(read, write)` — no session-id getter — and
  takes an `httpx2.AsyncClient` (the SDK's pinned HTTP namespace, a
  transitive dep exposed by its signature). `streamable_http_app(host=…)`
  only toggles the loopback DNS-rebinding/Host-Origin allowlist; the
  actual bind is uvicorn's job, so auth is a pure-ASGI middleware
  (`BearerOnly`, `secrets.compare_digest`) wrapping the Starlette app.
