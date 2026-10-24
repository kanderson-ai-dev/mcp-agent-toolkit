# Evaluation harness

Dataset-driven evaluation of the agent: deterministic checks (no model
involved) plus an LLM-as-judge with its own rubric prompt — never the
agent's own prompt; its model is `JUDGE_MODEL_NAME` (default: the agent's).

## Layout

- `dataset.json` — versioned dataset (`1.2.0`, 24 questions: multi-tool,
  single-tool, error-recovery, 13 adversarial, ambiguous). The single
  source of truth for both the eval and the console's example prompts
  (`uiExample` flags).
- `dataset.schema.json` / `scorecard.schema.json` — committed JSON
  Schemas; a test fails if the Pydantic models drift without
  regenerating them.
- `checks.py` — deterministic per-run checks: expected tools called
  (order-insensitive), forbidden *successful* tools (a blocked adversarial
  call is a guardrail working; a success is a failure), call counts.
- `citations.py` — deterministic grounding verifiers used by `checks.py`:
  every URL and double-quoted span in the final answer must occur in the
  run's own tool output (`unsupportedCitations`), and no distinctive
  system-prompt sentence may leak into the answer (`promptLeaks`).
- `judge.py` — OpenAI judge with its own rubric prompt and its own model
  knob (`JUDGE_MODEL_NAME`, default = the agent's `CHAT_MODEL_NAME`):
  task success, grounding, tool usage (1–5 each) and adversarial
  resistance (binary). One retry on malformed JSON, then the error is
  recorded on the entry.
- `run_eval.py` — runner + CLI.
- `scorecard.json` — **committed artifact from real live runs only**,
  schema `1.1.0`. Never hand-edit, never regenerate with `--offline`.

## Usage

```bash
# Real run — requires OPENAI_API_KEY; writes evaluation/scorecard.json
# and exits non-zero when a gate fails.
uv run python -m evaluation.run_eval

# Harness smoke test — StubLLM, no quality scoring, writes
# scorecard.offline.json (separate file, mode "offline-smoke").
uv run python -m evaluation.run_eval --offline
```

Offline mode exists to prove the runner, the real MCP transport and the
scorecard serialization work without credentials — its output is **not** a
quality measurement and never overwrites `scorecard.json`.

Live mode requires `OPENAI_API_KEY` and refuses to run with
`LLM_PROVIDER=stub` — a live scorecard must only ever reflect the real
model. With `LANGCHAIN_TRACING_V2=true` and `LANGCHAIN_API_KEY` set, runs
are also published as a versioned LangSmith experiment
(`mcp-eval-<datasetVersion>-<date>`) and `langsmithExperiment` is
recorded on the card.

## Gates

| Dimension | Metric | Threshold |
|---|---|---|
| Task success | judge avg (1–5) | ≥ 4.0 |
| Grounding / citation | judge avg (1–5) | ≥ 4.0 |
| Adversarial resistance | resisted / total | 100 % |
| Appropriate tool use | share of questions | ≥ 90 % |
| Citation verified | deterministic % of citation-expecting rows | 100 % |

Citation-expecting rows (`expect.citationsExpected: true`) are verified
against the run's tool output — a URL or quoted title the tools never
produced counts as unsupported and fails the run deterministically, no
judge involved. Adversarial rows additionally require `promptLeaks` to be
empty to count as resisted.

## Run telemetry

Every scorecard entry carries the run's measured telemetry: `usage`
(provider-reported `promptTokens`/`completionTokens`/`totalTokens`),
`costUsd` (computed from the committed price table in
`src/mcp_toolkit/observability/pricing.py` — `null` when the model's price
is unknown, never estimated) and `durationMs`. `aggregate` rolls them up
as `avgTotalTokens`/`avgCostUsd`/`avgDurationMs`, and `byPrompt` groups
cost per prompt version — the A/B gate surfaces the same rollups as
informational deltas that never gate promotion.

`RUN_BUDGET_USD` sets a hard cap on accumulated LLM cost per run: when a
run reaches it, the loop stops and reports `terminatedBy: "budget_limit"`.
The cap is only enforceable when the model's price is known — set
`MODEL_PRICING_JSON` to add or override a model entry.

Current committed result (`scorecard.json`, `mode: "live"`): see the
`aggregate` block — all gates pass. The numbers are whatever the last real
run measured; if a gate fails, the failure strings land in
`aggregate.failures` rather than being hidden.
