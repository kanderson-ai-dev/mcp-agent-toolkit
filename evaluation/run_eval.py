"""Evaluation harness — ``uv run python -m evaluation.run_eval`` (live)
or ``--offline``.

Live mode runs the versioned dataset against the real agent stack
(OpenAI + a real MCP server over stdio), applies deterministic checks
and an independent LLM-as-judge, and — when LangSmith credentials and
tracing are configured — publishes a versioned experiment. Live runs
write ``evaluation/scorecard.json``; offline smoke runs write
``scorecard.offline.json`` so the real scorecard is never clobbered by a
credential-free check. Offline mode is a harness smoke test over
StubLLM — it does NOT measure answer quality.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from evaluation.checks import DeterministicChecks, RecordedToolCall, check_run
from evaluation.dataset import EvalDataset, EvalQuestion, load_dataset
from evaluation.judge import JudgeError, JudgeInput, JudgeScores, judge_run
from evaluation.scorecard import (
    THRESHOLDS,
    Aggregate,
    CheckResult,
    PromptCostSummary,
    Scorecard,
    ScorecardEntry,
    ScorecardToolCall,
)
from mcp_toolkit.client.agent_loop import AgentLoopDeps, run_agent
from mcp_toolkit.client.events import (
    AgentEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from mcp_toolkit.client.llm.stub_llm import StubLLM
from mcp_toolkit.client.llm.types import LLMClient, OpenAIToolDef, TokenUsage
from mcp_toolkit.client.mcp_client import McpAgentClient, ToolInvoker, server_env
from mcp_toolkit.client.server_spawn import resolve_server_spawn
from mcp_toolkit.config import Settings, get_settings, load_env_file
from mcp_toolkit.observability.logging import configure_logging, get_logger
from mcp_toolkit.observability.pricing import parse_price_overrides, price_per_mtok
from mcp_toolkit.prompts.loader import LoadedPrompt, load_prompt

if TYPE_CHECKING:
    from langsmith import Client as LangSmithClient

EVAL_DIR = Path(__file__).resolve().parent
SCORECARD_LIVE = EVAL_DIR / "scorecard.json"
SCORECARD_OFFLINE = EVAL_DIR / "scorecard.offline.json"
OFFLINE_SUBSET = 3

log = get_logger("mcp_toolkit.eval")


@dataclass
class QuestionOutput:
    final_content: str
    tool_calls: list[RecordedToolCall]
    iterations: int
    terminated_by: str
    usage: TokenUsage | None = None
    cost_usd: float | None = None
    error: str | None = None


@dataclass
class EvalDeps:
    llm: LLMClient
    invoker: ToolInvoker
    tools: list[OpenAIToolDef]
    max_iterations: int
    agent_prompt: LoadedPrompt
    budget_usd: float | None = None
    price_per_mtok: tuple[float, float] | None = None


async def run_question(deps: EvalDeps, prompt: str) -> QuestionOutput:
    """Run one dataset question through the real agent, recording the stream."""
    calls: list[RecordedToolCall] = []
    pending: dict[str, RecordedToolCall] = {}

    def emit(event: AgentEvent) -> None:
        if isinstance(event, ToolCallEvent):
            call = RecordedToolCall(tool=event.tool, ok=False, args=event.args, duration_ms=0)
            pending[event.request_id] = call
            calls.append(call)
        elif isinstance(event, ToolResultEvent):
            existing = pending.get(event.request_id)
            if existing is not None:
                existing.ok = event.ok
                existing.duration_ms = event.duration_ms
                existing.output_preview = event.preview
                existing.output_full = (
                    json.dumps(event.result) if event.result is not None else event.preview
                )

    try:
        res = await run_agent(
            prompt,
            AgentLoopDeps(
                llm=deps.llm,
                invoker=deps.invoker,
                tools=deps.tools,
                max_iterations=deps.max_iterations,
                emit=emit,
                prompt=deps.agent_prompt,
                budget_usd=deps.budget_usd,
                price_per_mtok=deps.price_per_mtok,
            ),
        )
    except Exception as exc:
        return QuestionOutput(
            final_content="",
            tool_calls=calls,
            iterations=0,
            terminated_by="error",
            error=str(exc),
        )
    return QuestionOutput(
        final_content=res.content,
        tool_calls=calls,
        iterations=res.iterations,
        terminated_by=res.terminated_by,
        usage=res.usage,
        cost_usd=res.cost_usd,
    )


def _to_check_result(c: DeterministicChecks) -> CheckResult:
    return CheckResult(
        expected_tools_called=c.expected_tools_called,
        missing_tools=c.missing_tools,
        forbidden_successes=c.forbidden_successes,
        tool_call_count=c.tool_call_count,
        citations_expected=c.citations_expected,
        unsupported_citations=c.unsupported_citations or [],
        prompt_leaks=c.prompt_leaks or [],
    )


def build_entry(
    q: EvalQuestion,
    output: QuestionOutput,
    duration_ms: int,
    judge: JudgeScores | None = None,
    judge_error: str | None = None,
    prompt_version: dict[str, Any] | None = None,
    system_template: str = "",
) -> ScorecardEntry:
    return ScorecardEntry(
        id=q.id,
        category=q.category,
        prompt=q.prompt,
        prompt_version=prompt_version,
        tool_calls=[
            ScorecardToolCall(
                tool=c.tool,
                ok=c.ok,
                duration_ms=c.duration_ms,
                args=c.args,
                output_preview=c.output_preview,
            )
            for c in output.tool_calls
        ],
        iterations=output.iterations,
        terminated_by=output.terminated_by,
        duration_ms=duration_ms,
        usage=(
            {
                "promptTokens": output.usage.prompt_tokens,
                "completionTokens": output.usage.completion_tokens,
                "totalTokens": output.usage.total_tokens,
            }
            if output.usage is not None
            else None
        ),
        cost_usd=output.cost_usd,
        answer_preview=output.final_content[:800],
        checks=_to_check_result(
            check_run(q, output.tool_calls, output.final_content, system_template)
        ),
        judge=judge,
        judge_error=judge_error,
        error=output.error,
    )


def summarize(entries: list[ScorecardEntry], judged: bool) -> Aggregate:
    judges = [e.judge for e in entries if e.judge is not None]

    def avg(pick: Callable[[JudgeScores], float]) -> float | None:
        if not judged or not judges:
            return None
        return sum(pick(j) for j in judges) / len(judges)

    appropriate = sum(
        1 for e in entries if e.checks.expected_tools_called and not e.checks.forbidden_successes
    )
    tool_pct = appropriate / len(entries) if entries else 0.0

    adversarial = [e for e in entries if e.category == "adversarial"]
    resisted = sum(
        1
        for e in adversarial
        if not e.checks.forbidden_successes
        and not e.checks.prompt_leaks
        and e.judge is not None
        and e.judge.adversarial_resistance is not False
    )

    citation_rows = [e for e in entries if e.checks.citations_expected]
    citation_pct = (
        sum(1 for e in citation_rows if not e.checks.unsupported_citations) / len(citation_rows)
        if citation_rows
        else None
    )

    task_avg = avg(lambda j: j.task_success)
    ground_avg = avg(lambda j: j.grounding)
    tools_avg = avg(lambda j: j.tool_usage)

    def _prompt_key(e: ScorecardEntry) -> str:
        ref = e.prompt_version or {}
        return f"{ref.get('name', '?')}@v{ref.get('version', '?')}"

    def _token_avg(rows: list[ScorecardEntry]) -> float | None:
        vals = [float(e.usage["totalTokens"]) for e in rows if e.usage is not None]
        return sum(vals) / len(vals) if vals else None

    def _cost_avg(rows: list[ScorecardEntry]) -> float | None:
        vals = [e.cost_usd for e in rows if e.cost_usd is not None]
        return sum(vals) / len(vals) if vals else None

    def _duration_avg(rows: list[ScorecardEntry]) -> float | None:
        vals = [float(e.duration_ms) for e in rows if e.duration_ms > 0]
        return sum(vals) / len(vals) if vals else None

    avg_tokens = _token_avg(entries)
    avg_cost = _cost_avg(entries)
    groups: dict[str, list[ScorecardEntry]] = {}
    for e in entries:
        groups.setdefault(_prompt_key(e), []).append(e)
    by_prompt = {
        key: PromptCostSummary(
            runs=len(rows),
            avg_total_tokens=_token_avg(rows),
            avg_cost_usd=_cost_avg(rows),
            avg_duration_ms=_duration_avg(rows),
        )
        for key, rows in groups.items()
    }

    failures: list[str] = []
    if judged:
        if task_avg is not None and task_avg < THRESHOLDS["taskSuccess"]:
            failures.append(f"task_success {task_avg:.2f} < {THRESHOLDS['taskSuccess']}")
        if ground_avg is not None and ground_avg < THRESHOLDS["grounding"]:
            failures.append(f"grounding {ground_avg:.2f} < {THRESHOLDS['grounding']}")
        if adversarial and resisted < len(adversarial):
            failures.append(
                f"adversarial_resistance {resisted}/{len(adversarial)} < {len(adversarial)}"
            )
        if tool_pct < THRESHOLDS["toolUsageAppropriate"]:
            failures.append(
                f"tool_usage_appropriate {tool_pct * 100:.0f}% "
                f"< {THRESHOLDS['toolUsageAppropriate'] * 100:.0f}%"
            )
    if citation_pct is not None and citation_pct < THRESHOLDS["citationVerified"]:
        unsupported = sum(len(e.checks.unsupported_citations) for e in citation_rows)
        failures.append(
            f"citation_verified {citation_pct * 100:.0f}% "
            f"< {THRESHOLDS['citationVerified'] * 100:.0f}% ({unsupported} unsupported)"
        )

    return Aggregate(
        task_success_avg=task_avg,
        grounding_avg=ground_avg,
        tool_usage_avg=tools_avg,
        tool_usage_appropriate_pct=tool_pct,
        adversarial_resisted=f"{resisted}/{len(adversarial)}",
        citation_verified_pct=citation_pct,
        avg_total_tokens=avg_tokens,
        avg_cost_usd=avg_cost,
        avg_duration_ms=_duration_avg(entries),
        by_prompt=by_prompt,
        passed=not failures,
        failures=failures,
    )


JudgeFn = Any  # async (EvalQuestion, QuestionOutput) -> JudgeScores


async def _judge_or_error(
    judge_fn: Any, q: EvalQuestion, output: QuestionOutput
) -> tuple[JudgeScores | None, str | None]:
    if judge_fn is None:
        return None, None
    try:
        return await judge_fn(q, output), None
    except JudgeError as exc:
        return None, str(exc)


async def run_evaluation(
    questions: list[EvalQuestion],
    deps: EvalDeps,
    judge_fn: Any = None,
) -> list[ScorecardEntry]:
    """Pure core: run questions sequentially, judge each, build entries."""
    entries: list[ScorecardEntry] = []
    for q in questions:
        t0 = time.perf_counter()
        sys.stdout.write(f"  {q.id} {q.category} — running… ")
        sys.stdout.flush()
        output = await run_question(deps, q.prompt)
        duration_ms = round((time.perf_counter() - t0) * 1000)
        scores, judge_error = await _judge_or_error(judge_fn, q, output)
        entries.append(
            build_entry(
                q,
                output,
                duration_ms,
                scores,
                judge_error,
                prompt_version=deps.agent_prompt.ref,
                system_template=deps.agent_prompt.template,
            )
        )
        print(f"{duration_ms / 1000:.1f}s, {len(output.tool_calls)} tool calls")
    return entries


# ── LangSmith (optional) ──────────────────────────────────────────────


def _langsmith_active(settings: Settings, offline: bool) -> bool:
    return (
        not offline
        and settings.langchain_api_key is not None
        and settings.langchain_tracing_v2 == "true"
    )


def _ensure_ls_dataset(client: LangSmithClient, name: str, dataset: EvalDataset) -> str:
    """Ensure the dataset exists in LangSmith (create once, reuse after)."""
    from langsmith.schemas import DataType

    for ds in client.list_datasets(dataset_name=name):
        return str(ds.id)
    created = client.create_dataset(
        name,
        description=(f"mcp-agent-toolkit eval dataset v{dataset.version} — {dataset.description}"),
        data_type=DataType.kv,
    )
    client.create_examples(
        inputs=[{"questionId": q.id, "prompt": q.prompt} for q in dataset.questions],
        outputs=[
            {"category": q.category, "expect": q.expect.model_dump(by_alias=True)}
            for q in dataset.questions
        ],
        metadata=[{"datasetVersion": dataset.version} for _ in dataset.questions],
        dataset_id=created.id,
    )
    return name


async def _run_langsmith_experiment(
    settings: Settings,
    dataset: EvalDataset,
    deps: EvalDeps,
    judge_fn: Any,
    entries: list[ScorecardEntry],
) -> bool:
    """Drive the runs through ``langsmith.evaluate`` — produces a versioned
    experiment (one per dataset+date) with the same scores as evaluators.

    Returns False when the LangSmith path fails so the caller can fall
    back to the plain sequential loop.
    """
    try:
        from langsmith import Client
        from langsmith.evaluation import EvaluationResult, EvaluationResults, aevaluate
        from langsmith.schemas import Example, Run
    except ImportError:
        return False

    ds_name = f"mcp-agent-toolkit-eval-v{dataset.version}"
    judge_by_id: dict[str, JudgeScores] = {}
    by_id = {q.id: q for q in dataset.questions}

    def _output_wire(output: QuestionOutput) -> dict[str, Any]:
        return {
            "finalContent": output.final_content,
            "toolCalls": [
                {
                    "tool": c.tool,
                    "ok": c.ok,
                    "args": c.args,
                    "durationMs": c.duration_ms,
                    "outputPreview": c.output_preview,
                    "outputFull": c.output_full,
                }
                for c in output.tool_calls
            ],
            "iterations": output.iterations,
            "terminatedBy": output.terminated_by,
            "error": output.error,
        }

    def _output_from_wire(run: Run) -> QuestionOutput:
        raw: dict[str, Any] = run.outputs or {}
        return QuestionOutput(
            final_content=str(raw.get("finalContent") or ""),
            tool_calls=[
                RecordedToolCall(
                    tool=str(c.get("tool", "")),
                    ok=bool(c.get("ok")),
                    args=dict(c.get("args") or {}),
                    duration_ms=int(c.get("durationMs") or 0),
                    output_preview=str(c.get("outputPreview") or ""),
                    output_full=str(c.get("outputFull") or ""),
                )
                for c in raw.get("toolCalls") or []
                if isinstance(c, dict)
            ],
            iterations=int(raw.get("iterations") or 0),
            terminated_by=str(raw.get("terminatedBy") or "unknown"),
            error=raw.get("error") if isinstance(raw.get("error"), str) else None,
        )

    def _question_id(example: Example | None) -> str:
        if example is None:
            raise KeyError("LangSmith evaluator invoked without an example")
        return str((example.inputs or {})["questionId"])

    try:
        client = Client()
        _ensure_ls_dataset(client, ds_name, dataset)

        def require_q(question_id: str) -> EvalQuestion:
            q = by_id.get(question_id)
            if q is None:
                raise KeyError(f"dataset example has unknown questionId: {question_id}")
            return q

        async def target(inputs: dict[str, Any]) -> dict[str, Any]:
            q = require_q(str(inputs.get("questionId")))
            t0 = time.perf_counter()
            output = await run_question(deps, q.prompt)
            entries.append(
                build_entry(
                    q,
                    output,
                    round((time.perf_counter() - t0) * 1000),
                    prompt_version=deps.agent_prompt.ref,
                    system_template=deps.agent_prompt.template,
                )
            )
            return _output_wire(output)

        def deterministic_evaluator(run: Run, example: Example | None) -> EvaluationResults:
            q = require_q(_question_id(example))
            output = _output_from_wire(run)
            checks = check_run(
                q,
                output.tool_calls,
                output.final_content,
                deps.agent_prompt.template,
            )
            return EvaluationResults(
                results=[
                    EvaluationResult(
                        key="expected_tools",
                        score=1 if checks.expected_tools_called else 0,
                        comment=(
                            f"missing: {', '.join(checks.missing_tools)}"
                            if checks.missing_tools
                            else "all expected tools called"
                        ),
                    ),
                    EvaluationResult(
                        key="guardrail_respected",
                        score=0 if checks.forbidden_successes else 1,
                        comment=(
                            f"forbidden successful calls: {', '.join(checks.forbidden_successes)}"
                            if checks.forbidden_successes
                            else "no forbidden tool succeeded"
                        ),
                    ),
                    EvaluationResult(
                        key="citations_verified",
                        score=0 if checks.unsupported_citations else 1,
                        comment=(
                            f"unsupported: {', '.join(checks.unsupported_citations)}"
                            if checks.unsupported_citations
                            else "all citations verified"
                        ),
                    ),
                    EvaluationResult(
                        key="no_prompt_leak",
                        score=0 if checks.prompt_leaks else 1,
                        comment=(
                            f"leaked sentences: {len(checks.prompt_leaks)}"
                            if checks.prompt_leaks
                            else "no system-prompt leak"
                        ),
                    ),
                ]
            )

        async def judge_evaluator(run: Run, example: Example | None) -> EvaluationResults:
            if judge_fn is None:
                return EvaluationResults(
                    results=[EvaluationResult(key="judge_skipped", value="no OPENAI_API_KEY")]
                )
            q = require_q(_question_id(example))
            output = _output_from_wire(run)
            scores, _err = await _judge_or_error(judge_fn, q, output)
            if scores is None:
                return EvaluationResults(
                    results=[EvaluationResult(key="judge_error", value="unparseable judge output")]
                )
            judge_by_id[q.id] = scores
            results = [
                EvaluationResult(
                    key="task_success",
                    score=scores.task_success,
                    comment=scores.rationale,
                ),
                EvaluationResult(key="grounding", score=scores.grounding),
                EvaluationResult(key="tool_usage_judge", score=scores.tool_usage),
            ]
            if scores.adversarial_resistance is not None:
                results.append(
                    EvaluationResult(
                        key="adversarial_resistance",
                        score=1 if scores.adversarial_resistance else 0,
                    )
                )
            return EvaluationResults(results=results)

        await aevaluate(
            target,
            data=ds_name,
            evaluators=[deterministic_evaluator, judge_evaluator],
            experiment_prefix=f"mcp-eval-{dataset.version}-{datetime.now(UTC):%Y-%m-%d}",
            metadata={
                "datasetVersion": dataset.version,
                "model": settings.chat_model_name,
                "harness": "evaluation/run_eval.py",
                "agentSystemPromptVersion": deps.agent_prompt.version,
                "agentSystemPromptSha256": deps.agent_prompt.sha256,
            },
            max_concurrency=1,
        )
        # Merge judge scores gathered by the evaluator into entries.
        for e in entries:
            j = judge_by_id.get(e.id)
            if j is not None:
                e.judge = j
        return True
    except Exception as exc:
        log.warning("langsmith_experiment_failed", error=str(exc))
        entries.clear()
        return False


# ── CLI ───────────────────────────────────────────────────────────────


def _print_report(entries: list[ScorecardEntry], summary: Aggregate, judged: bool) -> None:
    print("\n── results ──────────────────────────────────────────")
    for e in entries:
        tools = ", ".join(f"{c.tool}{'' if c.ok else '✗'}" for c in e.tool_calls) or "none"
        judge_str = (
            f"task={e.judge.task_success:g} ground={e.judge.grounding:g} "
            f"tools={e.judge.tool_usage:g}"
            if e.judge
            else ("judge_error" if e.judge_error else "—")
        )
        print(f"{e.id:<6} {e.category:<15} tools=[{tools}]  judge: {judge_str}")
        if e.judge and e.judge.rationale:
            print(f"        {e.judge.rationale}")
        if e.judge_error:
            print(f"        judge error: {e.judge_error}")
    print("── aggregate ────────────────────────────────────────")

    def fmt(v: float | None) -> str:
        return f"{v:.2f}" if v is not None else "n/a"

    print(
        f"task_success avg:        {fmt(summary.task_success_avg)}  (≥{THRESHOLDS['taskSuccess']})"
    )
    print(f"grounding avg:           {fmt(summary.grounding_avg)}  (≥{THRESHOLDS['grounding']})")
    print(f"tool_usage judge avg:    {fmt(summary.tool_usage_avg)}")
    print(
        f"tool usage appropriate:  {summary.tool_usage_appropriate_pct * 100:.0f}%  "
        f"(≥{THRESHOLDS['toolUsageAppropriate'] * 100:.0f}%)"
    )
    print(f"adversarial resisted:    {summary.adversarial_resisted}  (must be all)")
    if summary.citation_verified_pct is not None:
        print(
            f"citations verified:      {summary.citation_verified_pct * 100:.0f}%  "
            f"(≥{THRESHOLDS['citationVerified'] * 100:.0f}% of citation-expecting)"
        )


async def _main() -> int:
    parser = argparse.ArgumentParser(prog="run_eval")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="StubLLM harness smoke test — no quality scoring",
    )
    args = parser.parse_args()
    offline: bool = args.offline

    load_env_file()
    settings = get_settings()
    configure_logging(settings.log_level)
    dataset = load_dataset()

    # Resolve both prompts from the registry before anything else — an
    # invalid registry or unknown version fails the run fast, and every
    # result records which prompt produced it.
    agent_prompt = load_prompt(
        "agent_system",
        settings.prompt_version_agent_system,
        root_dir=settings.prompt_registry_dir,
    )
    judge_prompt = load_prompt(
        "judge_rubric",
        settings.prompt_version_judge_rubric,
        root_dir=settings.prompt_registry_dir,
    )

    spawn = resolve_server_spawn(settings)
    env = server_env()
    if offline:
        # The harness smoke test must be credential- and network-free
        # even when .env configures a live search provider.
        env["SEARCH_PROVIDER"] = "none"
    async with McpAgentClient.connect_stdio(spawn.command, spawn.args, env=env) as mcp:
        tools = mcp.to_openai_tools()

        langsmith_active = _langsmith_active(settings, offline)
        if offline:
            print("eval mode: OFFLINE (StubLLM, harness smoke test — no quality scoring)")
        elif settings.openai_api_key is None:
            print(
                "eval requires OPENAI_API_KEY (or use --offline for the harness smoke test)",
                file=sys.stderr,
            )
            return 1
        else:
            print(
                f"eval mode: LIVE (model={settings.chat_model_name}, "
                f"langsmith={'on' if langsmith_active else 'off'})"
            )

        llm: LLMClient
        if offline:
            llm = StubLLM()
        else:
            # A "live" scorecard must only ever be produced by real OpenAI
            # calls — never let provider settings silently degrade to the
            # stub here.
            if settings.llm_provider == "stub":
                print(
                    "eval refuses to run 'live' with LLM_PROVIDER=stub — "
                    "the scorecard must reflect the real model",
                    file=sys.stderr,
                )
                return 1
            if settings.openai_api_key is None:
                return 1  # unreachable — verified before this branch
            from mcp_toolkit.client.llm.openai_llm import OpenAILLM

            llm = OpenAILLM(
                api_key=settings.openai_api_key.get_secret_value(),
                model=settings.chat_model_name,
            )
        deps = EvalDeps(
            llm=llm,
            invoker=mcp,
            tools=tools,
            max_iterations=settings.max_tool_iterations,
            agent_prompt=agent_prompt,
            budget_usd=settings.run_budget_usd,
            price_per_mtok=price_per_mtok(
                llm.model,
                parse_price_overrides(settings.model_pricing_json),
            ),
        )

        judge_fn = None
        if not offline and settings.openai_api_key is not None:
            from openai import AsyncOpenAI

            judge_client: Any = AsyncOpenAI(
                api_key=settings.openai_api_key.get_secret_value(),
                max_retries=2,
                timeout=60.0,
            )
            if langsmith_active:
                try:
                    from langsmith.wrappers import wrap_openai

                    judge_client = wrap_openai(judge_client)
                except ImportError:
                    pass

            async def judge_fn(
                q: EvalQuestion, o: QuestionOutput, _c: Any = judge_client
            ) -> JudgeScores:
                return await judge_run(
                    _c,
                    settings.chat_model_name,
                    JudgeInput(
                        question=q,
                        final_answer=o.final_content,
                        tool_calls=o.tool_calls,
                        terminated_by=o.terminated_by,
                    ),
                    rubric=judge_prompt.template,
                )

        entries: list[ScorecardEntry] = []
        langsmith_experiment = False
        if langsmith_active:
            langsmith_experiment = await _run_langsmith_experiment(
                settings, dataset, deps, judge_fn, entries
            )
        if not entries:
            subset = dataset.questions[:OFFLINE_SUBSET] if offline else dataset.questions
            entries = await run_evaluation(subset, deps, judge_fn)

    judged = not offline
    summary = summarize(entries, judged)
    scorecard = Scorecard(
        dataset_version=dataset.version,
        ran_at=datetime.now(UTC),
        mode="offline-smoke" if offline else "live",
        model="stub" if offline else settings.chat_model_name,
        judge_model=None if offline else settings.chat_model_name,
        langsmith_experiment=langsmith_experiment,
        aggregate=summary,
        prompts={
            "agent_system": agent_prompt.ref,
            "judge_rubric": judge_prompt.ref,
        },
        results=entries,
    )
    path = SCORECARD_OFFLINE if offline else SCORECARD_LIVE
    path.write_text(scorecard.model_dump_json(by_alias=True, indent=2), encoding="utf-8")

    _print_report(entries, summary, judged)
    print(f"scorecard → {path}")

    if not judged:
        print(
            "\nOFFLINE harness smoke test passed — runner, MCP transport and "
            "scorecard write all work."
        )
        return 0
    if not summary.passed:
        print(f"\nEVAL FAILED — thresholds not met: {'; '.join(summary.failures)}", file=sys.stderr)
        return 1
    print("\nEVAL PASSED — all thresholds met.")
    return 0


def main() -> int:
    # Windows consoles default to cp1252 — the report uses box-drawing
    # characters, so reconfigure streams rather than crash at the end.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError, OSError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    return asyncio.run(_main())


if __name__ == "__main__":
    sys.exit(main())
