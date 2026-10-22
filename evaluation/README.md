# Evaluation harness

Dataset-driven evaluation of the agent: deterministic checks (no model
involved) plus an independent LLM-as-judge — never the agent's own prompt.

## Layout

- `dataset.json` — versioned dataset (`1.1.0`, 14 questions: multi-tool,
  single-tool, error-recovery, adversarial, ambiguous). The single source
  of truth for both the eval and the console's example prompts
  (`uiExample` flags).
- `dataset.schema.json` / `scorecard.schema.json` — committed JSON
  Schemas; a test fails if the Pydantic models drift without
  regenerating them.
- `checks.py` — deterministic per-run checks: expected tools called
  (order-insensitive), forbidden *successful* tools (a blocked adversarial
  call is a guardrail working; a success is a failure), call counts.
- `judge.py` — independent OpenAI judge, own rubric prompt: task success,
  grounding, tool usage (1–5 each) and adversarial resistance (binary).
  One retry on malformed JSON, then the error is recorded on the entry.
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

Current committed result (`scorecard.json`, `mode: "live"`): see the
`aggregate` block — all gates pass. The numbers are whatever the last real
run measured; if a gate fails, the failure strings land in
`aggregate.failures` rather than being hidden.
