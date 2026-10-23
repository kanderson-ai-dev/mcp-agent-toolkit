#!/usr/bin/env python
"""Prompt Registry CLI — ``uv run python scripts/prompts_cli.py <cmd>``.

Commands: ``list``, ``show``, ``diff``, ``candidate``, ``ab``,
``promote``, ``rollback``, ``audit``.

``ab`` is a real evaluation by default — the dataset runs twice (or
once, scored under both rubrics, for ``judge_rubric``) through the live
agent (real OpenAI calls over real stdio MCP) plus the independent
judge. ``--offline`` is an explicit mechanics-only smoke test whose
result is never promotion evidence.

``promote`` requires that measured evidence (``promotionAllowed``) or
an explicit, audited ``--override-reason``. An offline or failed gate
can never promote silently.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from evaluation.dataset import EvalQuestion, load_dataset  # noqa: E402
from evaluation.judge import JudgeInput, JudgeScores, judge_run  # noqa: E402
from evaluation.run_eval import EvalDeps, QuestionOutput  # noqa: E402

from mcp_toolkit.client.llm.stub_llm import StubLLM  # noqa: E402
from mcp_toolkit.client.mcp_client import McpAgentClient, server_env  # noqa: E402
from mcp_toolkit.client.server_spawn import resolve_server_spawn  # noqa: E402
from mcp_toolkit.config import Settings, get_settings, load_env_file  # noqa: E402
from mcp_toolkit.observability.pricing import (  # noqa: E402
    parse_price_overrides,
    price_per_mtok,
)
from mcp_toolkit.prompts.ab_test import ABResult, run_ab  # noqa: E402
from mcp_toolkit.prompts.errors import PromptRegistryError  # noqa: E402
from mcp_toolkit.prompts.loader import LoadedPrompt  # noqa: E402
from mcp_toolkit.prompts.registry import PromptRegistry  # noqa: E402
from mcp_toolkit.prompts.schema import PromptArtifact  # noqa: E402
from mcp_toolkit.web.ab_runs import latest_ab_result, save_ab_result  # noqa: E402


def _registry(settings: Settings) -> PromptRegistry:
    return PromptRegistry(settings.prompt_registry_dir, audit_path=settings.prompt_audit_path)


def _loaded(artifact: PromptArtifact) -> LoadedPrompt:
    return LoadedPrompt(
        name=artifact.name,
        version=artifact.version,
        sha256=artifact.sha256,
        template=artifact.template,
    )


def _print_artifact(a: PromptArtifact) -> None:
    print(f"{a.name}@v{a.version} [{a.status}] sha={a.sha256[:12]}…  {a.description}")
    print(f"  author={a.author} created={a.created_at.isoformat()} model_hint={a.model_hint}")


def _resolve_candidate_file(raw: str) -> Path:
    """Template files must live inside the repository — never an
    arbitrary absolute path on the host."""
    path = (REPO_ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    if not path.is_relative_to(REPO_ROOT):
        raise SystemExit(f"template file must live inside the repository: {raw}")
    if not path.is_file():
        raise SystemExit(f"template file not found: {raw}")
    return path


def cmd_list(reg: PromptRegistry, _args: argparse.Namespace) -> int:
    artifacts = reg.list()
    if not artifacts:
        print("(registry is empty)")
        return 0
    current_name = ""
    for a in artifacts:
        if a.name != current_name:
            current_name = a.name
            print(f"\n{a.name}")
        promoted = " ← promoted" if a.status == "promoted" else ""
        print(f"  v{a.version:<3} {a.status:<12} {a.sha256[:12]}…  {a.description}{promoted}")
    return 0


def cmd_show(reg: PromptRegistry, args: argparse.Namespace) -> int:
    a = reg.get(args.name, args.version)
    _print_artifact(a)
    print("\n── template ─────────────────────────────────────────")
    print(a.template)
    return 0


def cmd_diff(reg: PromptRegistry, args: argparse.Namespace) -> int:
    text = reg.diff(args.name, args.from_version, args.to_version)
    print(text or "(no template differences)")
    return 0


def cmd_candidate(reg: PromptRegistry, args: argparse.Namespace) -> int:
    path = _resolve_candidate_file(args.file)
    template = path.read_text(encoding="utf-8")
    a = reg.create_candidate(
        args.name,
        template=template,
        description=args.description,
        author=args.author,
        model_hint=args.model_hint,
    )
    print(f"created candidate {a.name}@v{a.version} sha={a.sha256[:12]}…")
    return 0


async def _run_ab_live_or_smoke(
    name: str,
    baseline: LoadedPrompt,
    candidate: LoadedPrompt,
    settings: Settings,
    offline: bool,
) -> ABResult:
    """Real A/B over the live stack, or an explicitly-flagged smoke."""
    spawn = resolve_server_spawn(settings)
    env = server_env()
    if offline:
        env["SEARCH_PROVIDER"] = "none"
    async with McpAgentClient.connect_stdio(spawn.command, spawn.args, env=env) as mcp:
        judge: Any = None
        mode: str
        if offline:
            ab_llm: Any = StubLLM()
            mode = "offline-smoke"
        else:
            if settings.openai_api_key is None or settings.llm_provider == "stub":
                raise SystemExit(
                    "live A/B requires OPENAI_API_KEY (and LLM_PROVIDER != stub) — "
                    "use --offline for the mechanics-only smoke test"
                )
            from openai import AsyncOpenAI

            from mcp_toolkit.client.llm.openai_llm import OpenAILLM

            ab_llm = OpenAILLM(
                api_key=settings.openai_api_key.get_secret_value(),
                model=settings.chat_model_name,
            )
            judge_client = AsyncOpenAI(
                api_key=settings.openai_api_key.get_secret_value(),
                max_retries=2,
                timeout=60.0,
            )
            mode = "live"

            async def judge_fn(
                q: EvalQuestion, o: QuestionOutput, rubric: str, _c: Any = judge_client
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
                    rubric=rubric,
                )

            judge = judge_fn

        reg = _registry(settings)
        agent_prompt = _loaded(reg.get_promoted("agent_system"))
        judge_prompt = _loaded(reg.get_promoted("judge_rubric"))
        deps = EvalDeps(
            llm=ab_llm,
            invoker=mcp,
            tools=mcp.to_openai_tools(),
            max_iterations=settings.max_tool_iterations,
            agent_prompt=agent_prompt,
            budget_usd=settings.run_budget_usd,
            price_per_mtok=price_per_mtok(
                ab_llm.model,
                parse_price_overrides(settings.model_pricing_json),
            ),
        )

        def progress(msg: str) -> None:
            print(f"  {msg}", flush=True)

        return await run_ab(
            name,
            baseline,
            candidate,
            questions=load_dataset().questions,
            deps=deps,
            judge=judge,
            mode=mode,  # type: ignore[arg-type]
            judge_template=judge_prompt.template,
            agent_prompt=agent_prompt,
            on_progress=progress,
        )


def cmd_ab(reg: PromptRegistry, args: argparse.Namespace) -> int:
    baseline_art = (
        reg.get(args.name, args.baseline)
        if args.baseline is not None
        else reg.get_promoted(args.name)
    )
    candidate_art = reg.get(args.name, args.candidate)
    result = asyncio.run(
        _run_ab_live_or_smoke(
            args.name, _loaded(baseline_art), _loaded(candidate_art), args.settings, args.offline
        )
    )
    path = save_ab_result(result, out_dir=args.settings.prompt_ab_results_dir)
    reg.audit_log.append(
        {
            "type": "ab_test",
            "name": result.name,
            "baseline_version": result.baseline_version,
            "candidate_version": result.candidate_version,
            "mode": result.mode,
            "gate_passed": result.gate_passed,
            "deltas": result.deltas,
            "regressions": result.regressions,
            "run_id": path.stem,
        }
    )
    wire = result.to_wire()
    print(f"\nA/B result ({result.mode}) — saved to {path}")
    print(f"  baseline  v{result.baseline_version}: {wire['baseline']}")
    print(f"  candidate v{result.candidate_version}: {wire['candidate']}")
    print(f"  deltas:   {result.deltas}")
    if result.regressions:
        print("  regressions:")
        for r in result.regressions:
            print(f"    - {r}")
    print(f"  gate: {'PASSED' if result.gate_passed else 'FAILED'}")
    if result.mode != "live":
        print("  NOTE: offline smoke — not promotable evidence.")
    elif result.promotion_allowed:
        print(
            f"  promotable: prompts_cli.py promote {args.name} --version {result.candidate_version}"
        )
    else:
        print("  not promotable — fix regressions or use --override-reason (audited).")
    return 0


def cmd_promote(reg: PromptRegistry, args: argparse.Namespace) -> int:
    artifact = reg.get(args.name, args.version)
    if artifact.status != "candidate":
        raise SystemExit(
            f"{args.name}@v{args.version} is {artifact.status!r} — only candidates promote"
        )
    if args.override_reason:
        updated = reg.promote_override(args.name, args.version, reason=args.override_reason)
        print(
            f"OVERRIDE promotion: {args.name}@v{args.version} promoted "
            f"(reason audited: {args.override_reason})"
        )
        _print_artifact(updated)
        return 0
    settings = getattr(args, "settings", None)
    evidence = latest_ab_result(
        args.name,
        args.version,
        out_dir=settings.prompt_ab_results_dir if settings is not None else None,
    )
    if evidence is None:
        print(
            f"no completed A/B evidence for {args.name}@v{args.version} — run "
            f"`prompts_cli.py ab {args.name} --candidate {args.version}` first, "
            "or pass --override-reason",
            file=sys.stderr,
        )
        return 3
    if not evidence.get("promotionAllowed"):
        print("A/B gate failed — promotion blocked:", file=sys.stderr)
        for r in evidence.get("regressions", []):
            print(f"  - {r}", file=sys.stderr)
        print(
            f"(mode={evidence.get('mode')}; pass --override-reason to promote anyway — audited)",
            file=sys.stderr,
        )
        return 3
    updated = reg.promote(args.name, args.version, reason="A/B gate passed")
    print(f"promoted {args.name}@v{args.version} (gate passed, audited)")
    _print_artifact(updated)
    return 0


def cmd_rollback(reg: PromptRegistry, args: argparse.Namespace) -> int:
    restored = reg.rollback(args.name, args.to_version, reason=args.reason)
    print(f"rolled back: {args.name}@v{args.to_version} is promoted again")
    _print_artifact(restored)
    return 0


def cmd_audit(reg: PromptRegistry, args: argparse.Namespace) -> int:
    events = reg.audit_log.read()
    bad = reg.audit_log.verify_chain()
    for i, e in enumerate(events, start=1):
        marker = " ← TAMPERED" if bad is not None and i >= bad else ""
        detail = f"{e.get('name', '')} v{e.get('version', '')}".strip()
        print(f"{i:>4}  {e.get('type', '?'):<20} {detail}{marker}")
    if bad is None:
        print(f"\nchain verified — {len(events)} event(s), integrity OK")
    else:
        print(f"\nchain BROKEN at line {bad}", file=sys.stderr)
        return 3
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="prompts_cli", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list all artifacts")

    sp = sub.add_parser("show", help="show one artifact")
    sp.add_argument("name")
    sp.add_argument("version", type=int)

    sp = sub.add_parser("diff", help="unified diff between two versions")
    sp.add_argument("name")
    sp.add_argument("from_version", type=int)
    sp.add_argument("to_version", type=int)

    sp = sub.add_parser("candidate", help="create a candidate from a template file")
    sp.add_argument("name")
    sp.add_argument("--file", required=True, help="template path (inside the repository)")
    sp.add_argument("--description", required=True)
    sp.add_argument("--author", default="cli")
    sp.add_argument("--model-hint", default=None)

    sp = sub.add_parser("ab", help="A/B gate: baseline vs candidate over the dataset")
    sp.add_argument("name")
    sp.add_argument("--candidate", type=int, required=True)
    sp.add_argument("--baseline", type=int, default=None, help="default: promoted")
    sp.add_argument(
        "--offline",
        action="store_true",
        help="mechanics-only smoke (StubLLM, no judge) — never promotable",
    )

    sp = sub.add_parser("promote", help="promote a candidate (A/B-gated)")
    sp.add_argument("name")
    sp.add_argument("--version", type=int, required=True)
    sp.add_argument("--override-reason", default=None)

    sp = sub.add_parser("rollback", help="restore a previous version to promoted")
    sp.add_argument("name")
    sp.add_argument("--to-version", type=int, required=True)
    sp.add_argument("--reason", required=True)

    sub.add_parser("audit", help="print the audit chain and verify integrity")
    return p


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError, OSError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    args = _parser().parse_args()
    load_env_file()
    settings = get_settings()
    args.settings = settings
    try:
        reg = _registry(settings)
    except PromptRegistryError as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 2
    try:
        handler = {
            "list": cmd_list,
            "show": cmd_show,
            "diff": cmd_diff,
            "candidate": cmd_candidate,
            "ab": cmd_ab,
            "promote": cmd_promote,
            "rollback": cmd_rollback,
            "audit": cmd_audit,
        }[args.cmd]
        return int(handler(reg, args))
    except PromptRegistryError as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
