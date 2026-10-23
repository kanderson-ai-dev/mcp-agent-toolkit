# Security Policy

## Reporting

Report vulnerabilities privately to the maintainer (see `pyproject.toml`
authors). Do not open a public issue for a vulnerability until it has
been assessed and fixed.

## Threat model & controls

The toolkit processes **untrusted input twice**: the user's question and
the tool results the model reads (web pages, DB rows, file contents —
OWASP LLM01 *indirect prompt injection* is the distinctive risk of any
agent that reads external data).

| Risk | OWASP | Control |
|---|---|---|
| Path traversal / file exfiltration | A01 | `path_sandbox`: lexical *and* canonical containment; `..`, absolute escapes, NUL bytes, symlinks and Windows junctions/UNC all rejected |
| SQL injection / DB tampering | A03 | `db_query` read-only twice: statement guard (single SELECT/WITH, NFKC-normalized, forbidden-keyword + format-char smuggling rejected) **and** a `mode=ro` SQLite connection |
| Indirect prompt injection | LLM01 | Tool output is data, never instructions: sanitized + capped before reaching the model; never interpolated into system prompts |
| Resource exhaustion | LLM10 / A04 | Per-tool and per-session rate limiting; byte caps on file ops; char cap on tool output; `MAX_TOOL_ITERATIONS` bound |
| Secret leakage | A02 / LLM06 | `SecretStr` + empty=absent config; `*key*`/`*token*`/`*password*`/`*secret*` log redaction; logs carry argument *shape*, never values; server env allow-list keeps `OPENAI_API_KEY` out of the server process |
| Tool-abuse / SSRF-ish fetch | LLM07 | `web_search` only goes through the provider adapter — no raw URL fetching tool exists |
| Excessive agency | LLM08 | Four narrow tools; write surface limited to the sandbox; every block is a loud typed error + `mcp_guardrail_blocks_total` metric |
| Unbounded output to model | LLM01/A04 | `MAX_TOOL_OUTPUT_CHARS` truncation marked in the payload |
| Supply chain | A06 | Lockfile (`uv.lock`) pinned; `pip-audit` + `gitleaks` in CI; deps ≥7 days old preferred |

Additional notes:

- **stdio transport** — the server speaks MCP on stdout only; a malformed
  or hostile client cannot reach a network listener. The optional
  `/metrics` exposition binds `127.0.0.1` only.
- **Streamable HTTP transport (opt-in)** — `MCP_TRANSPORT=streamable-http`
  exposes the same tools over HTTP for clients that cannot spawn a
  subprocess. Loopback binds need no token; **any non-loopback bind
  requires `MCP_HTTP_TOKEN`** and the server refuses to start without one.
  Tokens are compared in constant time and only ever travel in the
  `Authorization` header. This transport is a developer surface, not
  hardened for the open Internet — put it behind TLS and a real
  identity layer for anything beyond localhost.
- **Error envelopes, not exceptions** — guardrail rejections return
  `{status:"error",code}` (`invalid_input`, `forbidden`, `not_found`,
  `rate_limited`, `upstream`, `internal`), so the model reacts to a
  typed signal instead of a crash.
- **Zero-leak CI** — the suite runs offline with `StubLLM` +
  `SEARCH_PROVIDER=none`; live credentials are opt-in env gates.
- **Web console (planned)** — the FastAPI console is a local,
  single-user adapter: not multi-tenant, not authenticated, not for
  direct Internet exposure without an added auth layer.
- **Prompt Registry mutations** — because the console is
  unauthenticated, every write (`candidates`, `ab-test`, `promote`,
  `rollback`) returns 403 unless `ENABLE_PROMPT_ADMIN=1` is set
  deliberately. Candidate templates are *data*: schema-validated, size-
  capped (≤ 8 000 chars / ≤ 32 KiB request body), rejected when they
  contain key-looking strings, and never executed or interpolated.
- **Evaluation honesty** — a prompt can only promote on a
  `mode: "live"` A/B result (real OpenAI agent + independent real
  judge); offline smoke results and failed gates return 409 unless a
  human supplies `overrideReason`, which lands in the audit log as
  `promoted_override` with the mandatory reason. The audit log itself
  is hash-chained: tampering with any historical entry is detectable
  via `verify_chain()` / `GET /api/prompts/audit`.

## Hardening checklist for deployers

1. Keep `SANDBOX_ROOT` dedicated — the only filesystem the tools touch.
2. `METRICS_PORT` binds loopback; do not publish it.
3. Rotate any key that ever appeared outside a secrets manager.
4. `SEARCH_PROVIDER=none` + `LLM_PROVIDER=stub` gives a fully offline,
   deterministic deployment for evaluation.
