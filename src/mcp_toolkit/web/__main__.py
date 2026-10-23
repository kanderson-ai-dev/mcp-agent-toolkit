"""Web console entrypoint — ``mcp-toolkit-web``.

The lifespan spawns the MCP server once per web process (not per
request), discovers tools, builds the LLM (same auto/stub selection as
the CLI) and serves the API plus the built console on one port. Closing
the lifespan drops the MCP child — no orphan processes.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI

from mcp_toolkit.client.llm import create_llm
from mcp_toolkit.client.mcp_client import McpAgentClient, server_env
from mcp_toolkit.client.server_spawn import resolve_server_spawn
from mcp_toolkit.config import Settings, get_settings, load_env_file
from mcp_toolkit.observability.logging import configure_logging, get_logger
from mcp_toolkit.observability.metrics import create_metrics
from mcp_toolkit.observability.pricing import parse_price_overrides, price_per_mtok
from mcp_toolkit.prompts.loader import LoadedPrompt, load_prompt
from mcp_toolkit.prompts.registry import PromptRegistry
from mcp_toolkit.server.streamable import is_loopback_host
from mcp_toolkit.web.ab_runs import AbRunManager
from mcp_toolkit.web.app import WebDeps, create_app
from mcp_toolkit.web.prompts_routes import PromptAPIDeps


def _utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError, OSError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def _seed_db_if_missing(db_path: Path) -> bool:
    """Seed the fixture DB when ``DB_PATH`` points at nothing — same
    contract as the CLI demo (``scripts.demo``): the console's
    ``db_query`` examples work out of the box, an existing database is
    never touched."""
    if db_path.exists():
        return False
    from scripts.seed_db import seed_demo_db

    seed_demo_db(db_path)
    return True


def build_app(settings: Settings) -> FastAPI:
    spawn = resolve_server_spawn(settings)
    metrics = create_metrics()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log = get_logger("mcp_toolkit.web")
        if await asyncio.to_thread(_seed_db_if_missing, settings.db_path):
            log.info("web_seed_db", path=str(settings.db_path))
        async with McpAgentClient.connect_stdio(spawn.command, spawn.args, env=server_env()) as mcp:
            llm = create_llm(settings)
            registry = PromptRegistry(
                settings.prompt_registry_dir, audit_path=settings.prompt_audit_path
            )
            # Registry resolution happens inside the lifespan — an invalid
            # registry or unknown version aborts startup (fail fast).
            agent_prompt = load_prompt(
                "agent_system",
                settings.prompt_version_agent_system,
                registry=registry,
            )
            tools = mcp.to_openai_tools()

            def prompt_provider() -> LoadedPrompt:
                # Re-resolved per run: a promotion takes effect on the next
                # run while in-flight runs keep their version (hot-swap).
                return load_prompt(
                    "agent_system",
                    settings.prompt_version_agent_system,
                    root_dir=settings.prompt_registry_dir,
                )

            def make_ab_runner(
                name: str,
                baseline: LoadedPrompt,
                candidate: LoadedPrompt,
                offline: bool,
                progress: Callable[[str], None],
            ) -> Any:
                return _run_ab(
                    name,
                    baseline,
                    candidate,
                    offline,
                    progress,
                    settings=settings,
                    registry=registry,
                    mcp=mcp,
                    tools=tools,
                )

            app.state.deps = WebDeps(
                invoker=mcp,
                tools=tools,
                discovered_tools=mcp.list_tools(),
                llm=llm,
                max_iterations=settings.max_tool_iterations,
                agent_prompt=agent_prompt,
                prompt_provider=prompt_provider,
                prompt_api=PromptAPIDeps(
                    registry=registry,
                    admin_enabled=settings.enable_prompt_admin,
                    ab_manager=AbRunManager(),
                    # Deployments without the top-level ``evaluation``
                    # package keep every other route; A/B answers 503.
                    make_ab_runner=make_ab_runner
                    if importlib.util.find_spec("evaluation") is not None
                    else None,
                    ab_results_dir=settings.prompt_ab_results_dir,
                ),
                metrics=metrics,
                cors_origin=settings.web_cors_origin or None,
                budget_usd=settings.run_budget_usd,
                # Priced by the *running* model — a stub's fabricated
                # usage never gets a real model's dollar figure.
                price_per_mtok=price_per_mtok(
                    llm.model,
                    parse_price_overrides(settings.model_pricing_json),
                ),
            )
            log.info(
                "web_listen",
                port=settings.web_port,
                llm=llm.name,
                tools=len(mcp.list_tools()),
                prompt_version=f"{agent_prompt.name}@v{agent_prompt.version}",
            )
            yield

    return create_app(lifespan=lifespan)


async def _run_ab(
    name: str,
    baseline: LoadedPrompt,
    candidate: LoadedPrompt,
    offline: bool,
    progress: Callable[[str], None],
    *,
    settings: Settings,
    registry: PromptRegistry,
    mcp: McpAgentClient,
    tools: Any,
) -> Any:
    """Build the A/B eval stack around the web process's live MCP
    connection, then run the gate. ``offline=True`` is an explicit,
    honest smoke test — never promotable; ``offline=False`` demands real
    OpenAI for both the agent and the judge."""
    try:
        from evaluation.dataset import load_dataset
        from evaluation.judge import JudgeInput, JudgeScores, judge_run
        from evaluation.run_eval import EvalDeps, QuestionOutput
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "A/B evaluation needs the top-level evaluation/ package — run "
            "from the repository checkout or an image that ships it"
        ) from exc

    from mcp_toolkit.client.llm.stub_llm import StubLLM
    from mcp_toolkit.client.llm.types import LLMClient, OpenAIToolDef
    from mcp_toolkit.prompts.ab_test import ABMode, ABResult, RubricJudge, run_ab

    openai_tools: list[OpenAIToolDef] = tools
    judge: RubricJudge | None = None
    mode: ABMode
    ab_llm: LLMClient
    if offline:
        ab_llm = StubLLM()
        mode = "offline-smoke"
        progress("mode: offline smoke (StubLLM — mechanics only, never promotable)")
    else:
        if settings.openai_api_key is None or settings.llm_provider == "stub":
            raise RuntimeError(
                "live A/B requires OPENAI_API_KEY and a non-stub LLM_PROVIDER — "
                "use offline: true for a harness smoke test"
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
            q: Any, o: QuestionOutput, rubric: str, _c: Any = judge_client
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

    agent_prompt = load_prompt(
        "agent_system", settings.prompt_version_agent_system, registry=registry
    )
    judge_prompt = load_prompt(
        "judge_rubric", settings.prompt_version_judge_rubric, registry=registry
    )
    deps = EvalDeps(
        llm=ab_llm,
        invoker=mcp,
        tools=openai_tools,
        max_iterations=settings.max_tool_iterations,
        agent_prompt=agent_prompt,
        budget_usd=settings.run_budget_usd,
        price_per_mtok=price_per_mtok(
            ab_llm.model,
            parse_price_overrides(settings.model_pricing_json),
        ),
    )
    result: ABResult = await run_ab(
        name,
        baseline,
        candidate,
        questions=load_dataset().questions,
        deps=deps,
        judge=judge,
        mode=mode,
        judge_template=judge_prompt.template,
        agent_prompt=agent_prompt,
        on_progress=progress,
    )
    progress(f"gate: {'PASSED' if result.gate_passed else 'FAILED'} (mode={mode})")
    return result


def main() -> None:
    _utf8_streams()
    load_env_file()
    settings = get_settings()
    configure_logging(settings.log_level)
    if not is_loopback_host(settings.web_host):
        get_logger("mcp_toolkit.web").warning(
            "web_non_loopback_bind",
            host=settings.web_host,
            hint="the console is unauthenticated — keep the published port "
            "loopback-only on the host (e.g. -p 127.0.0.1:3000:3000)",
        )
    uvicorn.run(
        build_app(settings),
        host=settings.web_host,
        port=settings.web_port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
