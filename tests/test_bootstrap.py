"""Bootstrap smoke test — the skeleton imports and the oracle fixtures parse."""

from __future__ import annotations

import json
from pathlib import Path

import mcp_toolkit

ORACLE = Path(__file__).parent / "fixtures" / "oracle"


def test_package_version() -> None:
    assert mcp_toolkit.__version__ == "0.1.0"


def test_oracle_fixtures_present_and_parse() -> None:
    dataset = json.loads((ORACLE / "dataset.json").read_text(encoding="utf-8"))
    assert dataset["version"] == "1.1.0"
    assert len(dataset["questions"]) == 14

    scorecard = json.loads((ORACLE / "scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["version"] == "1.0.0"
    assert scorecard["datasetVersion"] == "1.1.0"

    dump = json.loads((ORACLE / "sse-events.stub.json").read_text(encoding="utf-8"))
    assert dump["kind"] == "sse-event-dump"
    assert len(dump["runs"]) == 14
    assert {e["type"] for run in dump["runs"] for e in run["events"]} >= {
        "tool_call",
        "tool_result",
        "final",
    }
