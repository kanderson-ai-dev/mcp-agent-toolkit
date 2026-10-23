# prompts/ — Prompt Registry

Versioned prompt artifacts for `agent_system` (the agent loop's system
prompt) and `judge_rubric` (the evaluation judge's rubric). The
registry treats prompt quality the way CI treats code quality: a change
is measured against a dataset, gated, promoted, and every mutation is
recorded in a hash-chained audit log.

## Layout

```
prompts/
  agent_system/
    v1.yaml          # status: promoted
    v2.yaml          # status: candidate | rolled_back | draft
  judge_rubric/
    v1.yaml
data/prompt-registry/
  audit.jsonl        # append-only, hash-chained audit log
  ab/                # persisted A/B results (promote-time evidence)
```

Each artifact is YAML with `extra="forbid"` validation:
`name`, `version`, `status`, `author`, `created_at`, `description`,
`model_hint`, `template`.

## Lifecycle

```
draft → candidate → promoted → rolled_back → candidate → …
```

Exactly **one** `promoted` version per name at all times — promoting a
candidate atomically rolls the incumbent back to `rolled_back`.

## Runtime selection

| Env var | Values | Default |
|---|---|---|
| `PROMPT_REGISTRY_DIR` | registry root | `prompts` |
| `PROMPT_VERSION_AGENT_SYSTEM` | `latest-promoted` or a version number | `latest-promoted` |
| `PROMPT_VERSION_JUDGE_RUBRIC` | same | `latest-promoted` |

The web console resolves the prompt **per run**, so a promotion takes
effect on the next question while in-flight runs keep their version.
Every `final` event, metric, log and scorecard entry carries the
`{name, version, sha256}` provenance of the prompt that produced it.

## The A/B gate

`run_ab` (in `src/mcp_toolkit/prompts/ab_test.py`) runs the **same
dataset** against baseline and candidate under the **same judge
rubric**, then compares aggregates:

| Metric | Tolerance |
|---|---|
| task success (judge avg, 1–5) | regression > 0.1 fails |
| grounding (judge avg, 1–5) | regression > 0.1 fails |
| tool usage (judge avg, 1–5) | regression > 0.1 fails |
| appropriate tool use (0–1 share) | regression > 0.1 fails |
| adversarial resistance | **0 — must stay 100 %** |

Only a `mode: "live"` run — real OpenAI for agent *and* judge — is
promotion evidence. `mode: "offline-smoke"` (StubLLM, no judge) exists
to exercise the mechanics deterministically in CI and is **never**
promotable; the only escape is an explicit override, which is recorded
as a `promoted_override` audit event with a mandatory reason.

For `judge_rubric` A/B the rule differs by design: the dataset runs
**once** under the promoted `agent_system`, and the identical
transcripts are scored under both rubrics — only the judge prompt
varies. A new rubric is never auto-swapped into the agent loop.

## CLI

```bash
uv run python scripts/prompts_cli.py list
uv run python scripts/prompts_cli.py show agent_system 1
uv run python scripts/prompts_cli.py diff agent_system 1 2
uv run python scripts/prompts_cli.py candidate agent_system \
    --file prompts/drafts/v2.txt --description "tighter grounding rules"
uv run python scripts/prompts_cli.py ab agent_system --candidate 2          # live
uv run python scripts/prompts_cli.py ab agent_system --candidate 2 --offline  # smoke
uv run python scripts/prompts_cli.py promote agent_system --version 2
uv run python scripts/prompts_cli.py promote agent_system --version 2 \
    --override-reason "regression is judge-noise; reviewed by owner"
uv run python scripts/prompts_cli.py rollback agent_system --to-version 1 \
    --reason "v2 regressed grounding in production"
uv run python scripts/prompts_cli.py audit
```

## API

Reads are open on the local console; **mutations require
`ENABLE_PROMPT_ADMIN=1`** (the console is unauthenticated, so writes
are opt-in).

| Method | Path | Admin |
|---|---|---|
| `GET` | `/api/prompts` | – |
| `GET` | `/api/prompts/audit` | – |
| `GET` | `/api/prompts/{name}` | – |
| `GET` | `/api/prompts/{name}/{version}` | – |
| `GET` | `/api/prompts/{name}/diff?from=&to=` | – |
| `GET` | `/api/prompts/ab/{runId}` | – |
| `GET` | `/api/prompts/ab/{runId}/stream` (SSE progress) | – |
| `POST` | `/api/prompts/{name}/candidates` | ✓ |
| `POST` | `/api/prompts/{name}/ab-test` → `{runId}` | ✓ |
| `POST` | `/api/prompts/{name}/promote` | ✓ |
| `POST` | `/api/prompts/{name}/rollback` | ✓ |

`promote` returns **409** with the regression list when the gate fails
or no A/B evidence exists — unless `overrideReason` is supplied (always
audited). Templates are validated, size-capped (≤ 8 000 chars / ≤ 32
KiB) and rejected when they contain key-looking strings; they are data,
never executed.

## Audit log

`data/prompt-registry/audit.jsonl` — one JSON line per event
(`candidate_created`, `ab_test`, `promoted`, `promoted_override`,
`rolled_back`), each carrying `prevHash` + `hash` over the canonical
JSON. Any edit or deletion breaks the chain; `verify_chain()` (and
`GET /api/prompts/audit`, `… audit`) pinpoints the first tampered line.
