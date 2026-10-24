"""The ten UI-QA queries — single source of truth for the UI-QA suite.

Each query is verbatim the dataset prompt it maps to (the sync guard at
module scope fails loudly when the dataset drifts). `expected` carries
the scripted StubLLM plan the e2e assertions pin: friendly card titles
plus status badges in order.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class UiQuery:
    qid: str
    dataset_id: str
    prompt: str
    steps: list[tuple[str, str]]  # (friendly title, badge) in order
    viewports: list[tuple[int, int]] = field(default_factory=lambda: [(1280, 800)])


def _dataset_prompts() -> dict[str, str]:
    raw = json.loads((ROOT / "evaluation" / "dataset.json").read_text(encoding="utf-8"))
    return {q["id"]: q["prompt"] for q in raw["questions"]}


PROMPTS = _dataset_prompts()

_VIEWPORTS_QA = [(1280, 800), (768, 1024), (375, 812)]

UI_QUERIES: list[UiQuery] = [
    UiQuery(
        qid="q01",
        dataset_id="mt-01",
        prompt=PROMPTS["mt-01"],
        steps=[
            ("Database query", "Success"),
            ("Web search", "Success"),
            ("File write", "Success"),
        ],
        viewports=_VIEWPORTS_QA,
    ),
    UiQuery(
        qid="q02",
        dataset_id="st-01",
        prompt=PROMPTS["st-01"],
        steps=[("Database query", "Success")],
    ),
    UiQuery(
        qid="q03",
        dataset_id="er-01",
        prompt=PROMPTS["er-01"],
        steps=[("Database query", "Error"), ("Database query", "Success")],
    ),
    UiQuery(
        qid="q04",
        dataset_id="st-02",
        prompt=PROMPTS["st-02"],
        steps=[("Web search", "Success")],
    ),
    UiQuery(
        qid="q05",
        dataset_id="st-03",
        prompt=PROMPTS["st-03"],
        steps=[("File write", "Success")],
    ),
    UiQuery(
        qid="q06",
        dataset_id="st-04",
        prompt=PROMPTS["st-04"],
        steps=[("File read", "Success")],
        viewports=_VIEWPORTS_QA,
    ),
    UiQuery(
        qid="q07",
        dataset_id="ad-01",
        prompt=PROMPTS["ad-01"],
        steps=[("Database query", "Error")],
    ),
    UiQuery(
        qid="q08",
        dataset_id="ad-02",
        prompt=PROMPTS["ad-02"],
        steps=[("File read", "Error"), ("File read", "Error")],
    ),
    UiQuery(
        qid="q09",
        dataset_id="am-01",
        prompt=PROMPTS["am-01"],
        steps=[],
        viewports=_VIEWPORTS_QA,
    ),
    UiQuery(
        qid="q10",
        dataset_id="mt-02",
        prompt=PROMPTS["mt-02"],
        steps=[("Database query", "Success"), ("Web search", "Success")],
    ),
]

# The dataset is the behavioural pin — a query that drifts from the
# committed prompt text must fail loudly at import, not silently QA the
# wrong thing.
for q in UI_QUERIES:
    assert q.prompt in PROMPTS.values(), f"{q.qid}: prompt missing from dataset.json"
    assert PROMPTS[q.dataset_id] == q.prompt, f"{q.qid}: drifted from {q.dataset_id}"
