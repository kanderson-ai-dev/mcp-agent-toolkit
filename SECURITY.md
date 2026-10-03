# Security Policy

## Threat model

This project puts an LLM in charge of calling tools that touch the live
web, a database, and the filesystem. The attacker is therefore not only the
user — it is also **the content the tools return**: a web page can embed
instructions that try to hijack the agent (indirect prompt injection,
[OWASP LLM01](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)),
and a misbehaving model can try to step outside the sandbox or the
read-only SQL contract.

## Defenses implemented

| Layer | Mechanism | File |
|---|---|---|
| Indirect prompt injection (LLM01) | All tool output is sanitized before it re-enters the model context: control/bidi/zero-width characters are stripped, prompt-injection patterns are neutralized, and output is capped (`MAX_TOOL_OUTPUT_CHARS`). Tool content is treated as **data, never instructions** — it is never interpolated into a system prompt. | `src/server/guardrails/sanitize.ts` |
| Path traversal / sandbox escape | Lexical check (`..`, absolute paths) **and** canonical containment via `realpath` — defeats symlink/junction escapes. The sandbox root (`data/sandbox/`) is the only writable surface. | `src/server/guardrails/path-sandbox.ts` |
| SQL injection / write abuse | Defense in depth: a statement guard accepts only a single `SELECT`/`WITH` (denylist + terminator normalization) **and** the SQLite connection is opened `readonly`. | `src/server/guardrails/sql-guard.ts` + `src/server/tools/db-query.ts` |
| Runaway agent / cost abuse | Token-bucket rate limiter per tool per session (`RATE_LIMIT_PER_MINUTE`) plus a hard iteration bound on the agent loop (`MAX_TOOL_ITERATIONS`) — the loop is guaranteed to terminate. | `src/server/guardrails/rate-limiter.ts` + `src/client/agent-loop.ts` |
| Input validation | Every tool input is Zod-validated at the MCP boundary before touching a resource. | `src/server/tools/*.ts` |
| Secret hygiene | `OPENAI_API_KEY` is only ever read by the **client**; the spawned server receives an explicit allowlist of config vars — the key can never cross the process boundary. Empty/whitespace secrets count as absent. Logs redact `*_key`/`*_token`/`*_password`/`authorization`/`*_secret` fields. `.env` is gitignored; `gitleaks` runs in CI. | `src/client/mcp-client.ts` + `src/shared/config.ts` |
| Supply chain | `npm audit` in CI (high severity fails), lockfile committed, CI runs on `pull_request` with `permissions: contents: read`. | `.github/workflows/ci.yml` |

## Verified, not claimed

`tests/security/security.test.ts` fails the build if any of these stop
being true: `../` traversal rejected, absolute paths rejected, non-SELECT
statements rejected, rate limiter rejects over-quota calls on the real MCP
wire, injected tool output reaches the model sanitized.

## Web console (`src/web/`)

The Express adapter (`npm run web:api`) is a **local, single-user
console**, not a multi-tenant backend meant for direct Internet exposure:

- No authentication/authorization — anyone who can reach the port can ask
  questions and trigger tool calls. Run it on `localhost` or behind your
  own auth layer/reverse proxy if exposing it beyond your machine.
- One MCP server child process is shared across the whole web server
  lifetime (not per request); the rate limiter and sandbox guarantees
  from the MCP server above apply identically regardless of how many
  browser tabs connect.
- CORS is restricted to a single configurable dev origin
  (`WEB_CORS_ORIGIN`, default the Vite dev server) — the production build
  is served same-origin by the same Express process, so no CORS header is
  needed there at all.
- `POST /api/chat` validates the question with Zod (length-bounded) before
  it ever reaches the agent loop; `GET /api/chat/stream` run ids are
  single-use and TTL-expired to bound memory use.

## Scope notes (honest)

- The stdio transport trusts the spawning client — this is the MCP trust
  model for local servers. Remote deployment would need an HTTP transport
  with authentication, which is intentionally out of scope.
- Rate limiting is in-process; a horizontally scaled deployment needs a
  shared store.
- `db_query` read-only enforcement assumes a SQLite engine; porting to
  Postgres would add role-level `GRANT SELECT` as a third layer.
- The web console inherits the "no auth" scope note above — treat it as a
  local demo surface, not a hosted product, until an auth layer is added.

## Reporting

Please open a private security advisory on the repository rather than a
public issue for anything that looks exploitable.
