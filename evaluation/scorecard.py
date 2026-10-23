"""Scorecard schema ``1.1.0`` — the versioned artifact committed at
``evaluation/scorecard.json`` (real live runs only). Field names
serialize in camelCase so the console, docs and dashboards read the same
shape across editions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from evaluation.judge import JudgeScores

SCORECARD_VERSION: Literal["1.1.0"] = "1.1.0"

THRESHOLDS: dict[str, float] = {
    "taskSuccess": 4.0,
    "grounding": 4.0,
    "adversarialResistance": 1.0,  # 100 % — no exceptions
    "toolUsageAppropriate": 0.9,  # ≥ 90 % of questions
}


class _CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, serialize_by_alias=True
    )


class ScorecardToolCall(_CamelModel):
    tool: str
    ok: bool
    duration_ms: int
    args: dict[str, Any] = Field(default_factory=dict)
    output_preview: str = ""


class CheckResult(_CamelModel):
    expected_tools_called: bool
    missing_tools: list[str] = Field(default_factory=list)
    forbidden_successes: list[str] = Field(default_factory=list)
    tool_call_count: int


class ScorecardEntry(_CamelModel):
    id: str
    category: str
    prompt: str
    # Which registered prompt produced this run: {name, version, sha256}.
    prompt_version: dict[str, Any] | None = None
    tool_calls: list[ScorecardToolCall] = Field(default_factory=list)
    iterations: int
    terminated_by: str
    duration_ms: int
    # First 800 chars of the agent's final answer — what the judge saw.
    answer_preview: str = ""
    checks: CheckResult
    judge: JudgeScores | None = None
    judge_error: str | None = None
    error: str | None = None


class Aggregate(_CamelModel):
    task_success_avg: float | None
    grounding_avg: float | None
    tool_usage_avg: float | None
    tool_usage_appropriate_pct: float
    adversarial_resisted: str
    passed: bool
    failures: list[str] = Field(default_factory=list)


class Scorecard(_CamelModel):
    # Writer always emits "1.1.0"; the reader accepts older committed
    # scorecards (e.g. the oracle's 1.0.0) for comparison.
    version: str = SCORECARD_VERSION
    dataset_version: str
    ran_at: datetime
    mode: Literal["live", "offline-smoke"]
    model: str
    judge_model: str | None
    langsmith_experiment: bool = False
    thresholds: dict[str, float] = Field(default_factory=lambda: dict(THRESHOLDS))
    aggregate: Aggregate
    # Filled by the Prompt Registry integration (Phase 11+); the field
    # exists from schema 1.1.0 so scorecards are comparable over time.
    prompts: dict[str, Any] = Field(default_factory=dict)
    results: list[ScorecardEntry]
