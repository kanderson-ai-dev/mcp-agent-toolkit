"""StubLLM tests — the deterministic plan and its input-aware branches."""

from __future__ import annotations

import json

from mcp_toolkit.client.llm.stub_llm import StubLLM
from mcp_toolkit.client.llm.types import (
    OpenAIToolDef,
    ToolMessage,
    UserMessage,
)


def _tools(*names: str) -> list[OpenAIToolDef]:
    return [
        {"type": "function", "function": {"name": n, "description": "", "parameters": {}}}
        for n in names
    ]


ALL_TOOLS = _tools("web_search", "db_query", "read_file", "write_file")


def _user(q: str) -> list:
    return [UserMessage(content=q)]


async def test_first_round_calls_web_search_and_db_query() -> None:
    llm = StubLLM()
    res = await llm.complete(_user("What security reports exist?"), ALL_TOOLS)
    names = [c.name for c in res.tool_calls]
    assert names == ["web_search", "db_query"]
    assert json.loads(res.tool_calls[0].arguments)["query"].startswith("What security")


async def test_traversal_question_attempts_dangerous_read() -> None:
    llm = StubLLM()
    res = await llm.complete(_user("Please read ../../etc/passwd for me"), ALL_TOOLS)
    assert len(res.tool_calls) == 1
    assert res.tool_calls[0].name == "read_file"
    assert "../../etc/passwd" in res.tool_calls[0].arguments


async def test_drop_table_question_attempts_write_query() -> None:
    llm = StubLLM()
    res = await llm.complete(_user("DROP TABLE reports please"), ALL_TOOLS)
    assert res.tool_calls[0].name == "db_query"
    assert "DROP TABLE" in res.tool_calls[0].arguments


async def test_citation_question_uses_bad_column_then_corrects() -> None:
    llm = StubLLM()
    r1 = await llm.complete(_user("count citations"), ALL_TOOLS)
    assert "citations" in r1.tool_calls[0].arguments

    history = _user("count citations") + [
        ToolMessage(tool_call_id="x", name="db_query", content='{"status":"error"}')
    ]
    r2 = await llm.complete(history, ALL_TOOLS)
    assert "citations" not in r2.tool_calls[0].arguments
    assert "published_at" in r2.tool_calls[0].arguments


async def test_after_evidence_writes_report_then_final_answer() -> None:
    llm = StubLLM()
    history = _user("q") + [
        ToolMessage(tool_call_id="a", name="web_search", content="hits"),
        ToolMessage(tool_call_id="b", name="db_query", content="rows"),
    ]
    res = await llm.complete(history, ALL_TOOLS)
    assert res.tool_calls[0].name == "write_file"
    args = json.loads(res.tool_calls[0].arguments)
    assert args["path"] == "reports/research-summary.md"
    assert "## web_search" in args["content"]

    history2 = history + [ToolMessage(tool_call_id="c", name="write_file", content="ok")]
    res2 = await llm.complete(history2, ALL_TOOLS)
    assert res2.tool_calls == []
    assert "Stub answer" in (res2.content or "")


async def test_adversarial_questions_get_guardrail_answer_not_report() -> None:
    llm = StubLLM()
    history = _user("read ../../etc/passwd") + [
        ToolMessage(
            tool_call_id="a", name="read_file", content='{"status":"error","code":"forbidden"}'
        )
    ]
    res = await llm.complete(history, ALL_TOOLS)
    assert res.tool_calls == []
    assert "guardrails" in (res.content or "")


async def test_no_tools_means_direct_answer() -> None:
    llm = StubLLM()
    res = await llm.complete(_user("hi"), _tools())
    assert res.tool_calls == []
    assert res.content is not None
