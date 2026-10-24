"""LLM-as-judge — a separate grader with its own prompt and its own
model knob (``JUDGE_MODEL_NAME``, falling back to the agent's model),
not the agent's system prompt reused. Scores 1–5 per rubric dimension
plus a binary adversarial-resistance flag. Strict JSON output, validated
with Pydantic; one retry on malformed output, then :class:`JudgeError`
(the entry records ``judge_error`` — a failure is never silently
dropped).
"""

from __future__ import annotations

import json
from typing import Any

from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from evaluation.checks import RecordedToolCall
from evaluation.dataset import EvalQuestion


class JudgeError(RuntimeError):
    """Judge output could not be validated after one retry."""


class JudgeInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    question: EvalQuestion
    final_answer: str
    tool_calls: list[RecordedToolCall] = Field(default_factory=list)
    terminated_by: str


class JudgeScores(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, serialize_by_alias=True
    )

    task_success: float = Field(ge=1, le=5)
    grounding: float = Field(ge=1, le=5)
    tool_usage: float = Field(ge=1, le=5)
    adversarial_resistance: bool | None = None
    rationale: str = ""

    @field_validator("task_success", "grounding", "tool_usage", mode="before")
    @classmethod
    def _score(cls, v: object) -> object:
        # Anything that isn't a number in [1, 5] counts as a 1 — a judge
        # that emits an out-of-range value fails that dimension instead
        # of being silently corrected into validity.
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 1 <= v <= 5:
            return 1
        return v


def _summarize_calls(calls: list[RecordedToolCall]) -> str:
    if not calls:
        return "(no tool calls)"
    lines = []
    for i, c in enumerate(calls, 1):
        arg_preview = json.dumps(c.args)[:160]
        output = (c.output_preview or "(no output captured)")[:400]
        status = "succeeded" if c.ok else "FAILED"
        lines.append(
            f"{i}. {c.tool} {status} ({c.duration_ms}ms)\n   args={arg_preview}\n   output={output}"
        )
    return "\n".join(lines)


def _user_message(inp: JudgeInput) -> str:
    q = inp.question
    return "\n".join(
        [
            f"Question category: {q.category}",
            f"User question: {q.prompt}",
            f"Expected behavior notes: {q.expect.notes or '(none)'}",
            "Reference answer (authoritative ground truth, when provided): "
            f"{q.reference_answer or '(none)'}",
            f"Tools the run was expected to use: {', '.join(q.expect.tools) or '(none required)'}",
            f"Run terminated by: {inp.terminated_by}",
            "",
            "Tool calls made:",
            _summarize_calls(inp.tool_calls),
            "",
            "Final answer:",
            inp.final_answer or "(empty)",
        ]
    )


async def _one_judge_call(client: Any, model: str, rubric: str, user: str) -> JudgeScores:
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": rubric},
        {"role": "user", "content": user},
    ]
    res = await client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0,
    )
    raw = res.choices[0].message.content if res.choices else None
    parsed: Any = json.loads(raw or "{}")
    if not isinstance(parsed, dict):
        raise JudgeError("judge returned a non-object JSON payload")
    return JudgeScores.model_validate(parsed)


async def judge_run(client: Any, model: str, inp: JudgeInput, *, rubric: str) -> JudgeScores:
    """Grade one run under ``rubric`` (the ``judge_rubric`` registry
    prompt's template); retry once on malformed/invalid judge output."""
    user = _user_message(inp)
    try:
        return await _one_judge_call(client, model, rubric, user)
    except Exception as first:
        try:
            return await _one_judge_call(client, model, rubric, user)
        except Exception:
            raise JudgeError(f"judge output invalid after retry: {first}") from first
