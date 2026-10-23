"""Typed access to the versioned evaluation dataset (``dataset.json``).

The same file drives three consumers: this eval harness, the console's
example chips (injected at build time) and the e2e suite — one source of
truth so a question is never redefined in two places. The JSON schema is
committed at ``dataset.schema.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

EvalCategory = Literal["multi_tool", "single_tool", "error_recovery", "adversarial", "ambiguous"]


class _CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, serialize_by_alias=True
    )


class QuestionExpect(_CamelModel):
    tools: list[str] = Field(default_factory=list)
    forbidden_successful_tools: list[str] = Field(default_factory=list)
    # The answer is expected to cite tool-produced evidence (URLs, DB
    # titles); the deterministic citation gate applies to these rows.
    citations_expected: bool = False
    notes: str = ""


class EvalQuestion(_CamelModel):
    id: str = Field(min_length=1)
    category: EvalCategory
    ui_example: bool = False
    prompt: str = Field(min_length=1)
    # Optional gold answer — ground truth the judge verifies claims
    # against when the correct output is deterministic (e.g. seeded rows).
    reference_answer: str | None = None
    expect: QuestionExpect


class EvalDataset(_CamelModel):
    version: str = Field(min_length=1)
    description: str = ""
    categories: dict[str, str] = Field(default_factory=dict)
    questions: list[EvalQuestion] = Field(min_length=1)


DATASET_PATH = Path(__file__).resolve().parent / "dataset.json"


def load_dataset(path: Path = DATASET_PATH) -> EvalDataset:
    return EvalDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))
