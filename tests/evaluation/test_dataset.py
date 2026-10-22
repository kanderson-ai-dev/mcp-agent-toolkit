"""Dataset loader tests — the versioned file is the single source of truth."""

from __future__ import annotations

import json

import pytest
from evaluation.dataset import EvalDataset, load_dataset
from pydantic import ValidationError


def test_loads_versioned_dataset() -> None:
    ds = load_dataset()
    assert ds.version == "1.1.0"
    assert len(ds.questions) == 14
    assert {q.category for q in ds.questions} == {
        "multi_tool",
        "single_tool",
        "error_recovery",
        "adversarial",
        "ambiguous",
    }
    assert sum(1 for q in ds.questions if q.ui_example) >= 6
    by_id = {q.id: q for q in ds.questions}
    assert by_id["mt-01"].expect.tools == ["db_query", "web_search", "write_file"]
    assert by_id["ad-01"].expect.forbidden_successful_tools == [
        "db_query",
        "write_file",
    ]
    assert by_id["st-01"].reference_answer is not None


def test_question_ids_are_unique() -> None:
    ids = [q.id for q in load_dataset().questions]
    assert len(ids) == len(set(ids))


def test_invalid_dataset_rejected(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"version": "", "questions": []}), encoding="utf-8")
    with pytest.raises(ValidationError):
        EvalDataset.model_validate(json.loads(bad.read_text()))


def test_unknown_category_rejected() -> None:
    ds = json.loads(load_dataset().model_dump_json(by_alias=True))
    ds["questions"][0]["category"] = "bogus"
    with pytest.raises(ValidationError):
        EvalDataset.model_validate(ds)
