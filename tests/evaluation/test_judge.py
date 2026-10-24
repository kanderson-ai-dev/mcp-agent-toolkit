"""Judge tests — strict JSON parsing, retry, and failure recording."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from evaluation.checks import RecordedToolCall
from evaluation.dataset import load_dataset
from evaluation.judge import JudgeError, JudgeInput, judge_run
from evaluation.run_eval import QuestionOutput

RUBRIC = "Grade strictly."


def _question(qid: str = "st-01"):
    return next(q for q in load_dataset().questions if q.id == qid)


def _input(qid: str = "st-01") -> JudgeInput:
    return JudgeInput(
        question=_question(qid),
        final_answer="Two reports cover security.",
        tool_calls=[
            RecordedToolCall(tool="db_query", ok=True, args={"query": "SELECT 1"}, duration_ms=4)
        ],
        terminated_by="answer",
    )


class FakeCompletions:
    """Canned chat-completions endpoint — records calls, replays payloads."""

    def __init__(self, payloads: list[str]) -> None:
        self.payloads = list(payloads)
        self.calls = 0

    async def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        content = self.payloads.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _client(payloads: list[str]) -> Any:
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(payloads)))


GOOD = json.dumps(
    {
        "task_success": 5,
        "grounding": 4,
        "tool_usage": 5,
        "adversarial_resistance": None,
        "rationale": "Correct tools, faithful answer.",
    }
)


async def test_valid_judge_output_parses() -> None:
    client = _client([GOOD])
    scores = await judge_run(client, "gpt-4o-mini", _input(), rubric=RUBRIC)
    assert scores.task_success == 5
    assert scores.grounding == 4
    assert scores.tool_usage == 5
    assert scores.adversarial_resistance is None
    assert scores.rationale


async def test_out_of_range_scores_fail_to_one() -> None:
    payload = json.dumps(
        {"task_success": 99, "grounding": "high", "tool_usage": 3, "rationale": "x"}
    )
    scores = await judge_run(_client([payload]), "m", _input(), rubric=RUBRIC)
    assert scores.task_success == 1  # 99 is out of range → 1
    assert scores.grounding == 1  # non-numeric → 1
    assert scores.tool_usage == 3


async def test_malformed_output_retries_once_then_raises() -> None:
    client = _client(["{not json", GOOD])
    scores = await judge_run(client, "m", _input(), rubric=RUBRIC)
    assert scores.task_success == 5
    assert client.chat.completions.calls == 2

    broken = _client(["{not json", "also not json"])
    with pytest.raises(JudgeError):
        await judge_run(broken, "m", _input(), rubric=RUBRIC)
    assert broken.chat.completions.calls == 2


async def test_non_object_json_raises_judge_error() -> None:
    client = _client(["[1,2,3]", "[4,5,6]"])
    with pytest.raises(JudgeError):
        await judge_run(client, "m", _input(), rubric=RUBRIC)


async def test_adversarial_resistance_flag_parses() -> None:
    payload = json.dumps(
        {
            "task_success": 5,
            "grounding": 5,
            "tool_usage": 5,
            "adversarial_resistance": True,
            "rationale": "Refused the DROP TABLE.",
        }
    )
    scores = await judge_run(_client([payload]), "m", _input("ad-01"), rubric=RUBRIC)
    assert scores.adversarial_resistance is True


class RecordingCompletions:
    """Records the kwargs of every create() call — proves which model the
    judge factories actually send, then replays ``GOOD``."""

    def __init__(self) -> None:
        self.kw: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.kw.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=GOOD))])


def _recording_client() -> tuple[Any, RecordingCompletions]:
    c = RecordingCompletions()
    return SimpleNamespace(chat=SimpleNamespace(completions=c)), c


def _output() -> QuestionOutput:
    return QuestionOutput(
        final_content="answer", tool_calls=[], iterations=1, terminated_by="answer"
    )


async def test_eval_judge_fn_forwards_the_configured_model() -> None:
    from evaluation.run_eval import _judge_fn

    client, comp = _recording_client()
    scores = await _judge_fn(client, "judge-model-x", RUBRIC)(_question(), _output())
    assert comp.kw[0]["model"] == "judge-model-x"
    assert scores.task_success == 5


async def test_web_judge_fn_forwards_the_configured_model() -> None:
    from mcp_toolkit.web.__main__ import _judge_fn

    client, comp = _recording_client()
    await _judge_fn(client, "judge-model-y")(_question(), _output(), RUBRIC)
    assert comp.kw[0]["model"] == "judge-model-y"


async def test_cli_judge_fn_forwards_the_configured_model() -> None:
    from scripts.prompts_cli import _judge_fn

    client, comp = _recording_client()
    await _judge_fn(client, "judge-model-z")(_question(), _output(), RUBRIC)
    assert comp.kw[0]["model"] == "judge-model-z"
