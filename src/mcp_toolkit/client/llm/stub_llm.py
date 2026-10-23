"""Deterministic stand-in for a real LLM.

Replays a keyword-driven tool-call plan so the whole agent loop is
exercisable offline, in tests, and in CI with zero secrets — and so every
dataset question and scripted console query produces a stable card
sequence end to end.

Input-aware branches keep determinism while exercising the guardrail
and error-recovery paths end-to-end:

- Path traversal deliberately attempts the dangerous call — twice when
  the question supplies an absolute fallback path — so the server's
  typed ``forbidden`` error renders in the transcript.
- ``DROP TABLE`` likewise attempts the dangerous write.
- Indirect-injection phrasing searches the web once, then answers with a
  refusal — never a write.
- "citation" questions trigger a nonexistent-column error and then a
  corrected retry (error-recovery path).
- Otherwise tools are selected by question keywords in canonical order
  ``db_query → read_file → web_search``; ``write_file`` follows the
  evidence round when the question asks to persist something, or runs
  immediately for write-only requests. Questions matching no keyword
  get a direct answer with no tool calls.
"""

from __future__ import annotations

import itertools
import json
import re
from collections.abc import Callable, Sequence

from mcp_toolkit.client.llm.types import (
    AgentMessage,
    LLMResponse,
    OpenAIToolDef,
    TokenUsage,
    ToolCallDef,
    ToolMessage,
    UserMessage,
)

_ids = itertools.count(1)


def _next_id() -> str:
    return f"stubcall_{next(_ids)}"


_TRAVERSAL = re.compile(r"\.\.[\\/]|etc/passwd|etc/shadow", re.IGNORECASE)
_ABS_PATH = re.compile(r"[A-Za-z]:[\\/][^`\s'\"]+|/(?:etc|usr|var|root|home)/[^`\s'\"]+")
_BACKTICK = re.compile(r"`([^`]{1,400})`")
_TRAV_PATH = re.compile(r"\.\.[\\/][^`\s'\"]+")
_DROP_TABLE = re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE)
_SQLI = re.compile(
    r"\bPRAGMA\b|\bATTACH\b|;\s*(?:DROP|PRAGMA|ATTACH|SELECT|INSERT|DELETE|UPDATE)",
    re.IGNORECASE,
)
_STORM = re.compile(r"\b(\d{2,4})\s*times\b|rapid succession|rate.?limit", re.IGNORECASE)
_BIG_WRITE = re.compile(r"\b\d+\s*MB\b|megabyte|zero bytes", re.IGNORECASE)
_BASE64 = re.compile(r"\bbase64\b|\bdecode\b", re.IGNORECASE)
_INJECTION = re.compile(
    r"addressed to you|previous instructions|system prompt|"
    r"ignore (your |all |the )?(previous|prior)|reveal|pretend|role.?play|act as|"
    r"exact instructions|configured with|your instructions",
    re.IGNORECASE,
)
_CITATION = re.compile(r"citation", re.IGNORECASE)

_READ = re.compile(r"\bread\b|contents?\b|\.txt\b|symlink", re.IGNORECASE)
_WEB = re.compile(r"\bweb\b|internet|cite|source|research|search", re.IGNORECASE)
_DB = re.compile(r"\breports?\b|database|catalog|\bauthor", re.IGNORECASE)
_WRITE = re.compile(r"\bsave|\bwrite\b|persist|briefing|notes/", re.IGNORECASE)


def _escape_targets(question: str) -> tuple[list[str], list[str]]:
    """(dangerous, benign-relative) paths mentioned in the question.

    Dangerous = `..` traversal or an absolute/drive path — the sandbox
    must reject these. Benign backticked relative paths (a symlink name,
    a workspace file) are listed separately so adversarial reads can
    attempt them first and still get blocked downstream.
    """
    dangerous: list[str] = []
    benign: list[str] = []
    for text in _BACKTICK.findall(question):
        t = text.strip()
        if not t or " " in t or ";" in t:
            continue  # prose or SQL in backticks, not a path
        if _TRAVERSAL.search(t) or _ABS_PATH.search(t):
            target_list = dangerous
        elif "/" in t or "\\" in t or "." in t:
            target_list = benign
        else:
            continue
        if t not in target_list:
            target_list.append(t)
    for m in _TRAV_PATH.finditer(question):
        p = m.group(0).rstrip("`.,;'\"")
        if p not in dangerous:
            dangerous.append(p)
    for m in _ABS_PATH.finditer(question):
        p = m.group(0).rstrip("`.,;'\"")
        # An absolute path embedded inside a traversal (`../../etc/passwd`
        # contains `/etc/passwd`) must not produce a second attempt.
        if p not in dangerous and not any(p in d or d in p for d in dangerous):
            dangerous.append(p)
    return dangerous, benign


def _tool_rounds(messages: list[AgentMessage]) -> int:
    return sum(isinstance(m, ToolMessage) for m in messages)


def _tool_calls_by(messages: list[AgentMessage], name: str) -> int:
    return sum(isinstance(m, ToolMessage) and m.name == name for m in messages)


def _already_wrote(messages: list[AgentMessage]) -> bool:
    return any(isinstance(m, ToolMessage) and m.name == "write_file" for m in messages)


def _call(name: str, args: dict[str, object]) -> ToolCallDef:
    return ToolCallDef(id=_next_id(), name=name, arguments=json.dumps(args))


def _fake_usage(
    messages: list[AgentMessage], content: str | None, calls: Sequence[ToolCallDef]
) -> TokenUsage:
    """Deterministic stand-in usage — derived from message sizes so runs
    are reproducible. These are **fabricated** counts (the stub never
    calls a model); the ``stub`` model has no price entry, so the wire
    ``costUsd`` for stub runs is always ``null``."""
    prompt_chars = sum(
        len(m.content or "") + sum(len(c.arguments) for c in getattr(m, "tool_calls", ()))
        for m in messages
    )
    completion_chars = len(content or "") + sum(len(c.arguments) for c in calls)
    return TokenUsage(
        prompt_tokens=max(1, prompt_chars // 4),
        completion_tokens=max(1, completion_chars // 4),
    )


def _resp(
    messages: list[AgentMessage], content: str | None, calls: Sequence[ToolCallDef]
) -> LLMResponse:
    return LLMResponse(
        content=content, tool_calls=calls, usage=_fake_usage(messages, content, calls)
    )


def _delta_chunks(content: str) -> list[str]:
    """Deterministic token-level chunks — small word groups so SSE
    consumers see several ``answer_delta`` frames whose concatenation is
    byte-identical to the final content."""
    words = content.split(" ")
    return [
        " ".join(words[i : i + 4]) + (" " if i + 4 < len(words) else "")
        for i in range(0, len(words), 4)
    ]


def _build_stub_report(messages: list[AgentMessage]) -> str:
    collected = "\n".join(
        f"## {m.name}\n\n{m.content[:800]}\n" for m in messages if isinstance(m, ToolMessage)
    )
    return "\n".join(
        [
            "# Research summary (stub run)",
            "",
            "Generated by the deterministic StubLLM — no OPENAI_API_KEY present.",
            "Each section below is raw output collected over the MCP protocol.",
            "",
            collected,
        ]
    )


class StubLLM:
    name = "stub"
    model = "stub"
    """Unpriced by design — fabricated usage must never show a real cost."""

    async def complete(
        self,
        messages: list[AgentMessage],
        tools: list[OpenAIToolDef],
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        def has(tool_name: str) -> bool:
            return any(t["function"]["name"] == tool_name for t in tools)

        question = next(
            (m.content for m in reversed(messages) if isinstance(m, UserMessage)),
            "the user's question",
        )
        storm = _STORM.search(question)
        dangerous, benign = _escape_targets(question)
        escape = bool(dangerous)
        wants_write = bool(_WRITE.search(question))
        sqli = bool(_SQLI.search(question))
        drop = bool(_DROP_TABLE.search(question))
        big_write = bool(_BIG_WRITE.search(question))
        injection = bool(_INJECTION.search(question)) or bool(_BASE64.search(question))
        adversarial = escape or drop or sqli or big_write or injection or bool(storm)
        rounds = _tool_rounds(messages)

        if rounds == 0:
            if storm and has("db_query"):
                count_m = re.search(r"\b(\d{2,4})\s*times\b", question)
                count = min(int(count_m.group(1)) if count_m else 65, 70)
                sql = next(
                    (t for t in _BACKTICK.findall(question) if "SELECT" in t.upper()),
                    "SELECT 1",
                )
                return _resp(
                    messages, None, [_call("db_query", {"query": sql}) for _ in range(count)]
                )
            if big_write and wants_write and has("write_file"):
                path = next(
                    (t.strip() for t in _BACKTICK.findall(question) if " " not in t.strip()),
                    "dump.bin",
                )
                return _resp(
                    messages,
                    None,
                    [_call("write_file", {"path": path, "content": "0" * 1_100_000})],
                )
            if escape and wants_write and has("write_file"):
                return _resp(
                    messages,
                    None,
                    [
                        _call(
                            "write_file",
                            {"path": dangerous[0], "content": "stub payload"},
                        )
                    ],
                )
            if escape and has("read_file"):
                calls = [_call("read_file", {"path": p}) for p in benign + dangerous]
                return _resp(messages, None, calls)
            if sqli and has("db_query"):
                sql = next(
                    (t for t in _BACKTICK.findall(question) if ";" in t),
                    "PRAGMA journal_mode=WAL",
                )
                return _resp(messages, None, [_call("db_query", {"query": sql})])
            if drop and has("db_query"):
                return _resp(messages, None, [_call("db_query", {"query": "DROP TABLE reports"})])
            if injection:
                calls = []
                if _WEB.search(question) and has("web_search"):
                    calls.append(
                        _call(
                            "web_search",
                            {"query": "Model Context Protocol", "max_results": 3},
                        )
                    )
                if calls:
                    return _resp(messages, None, calls)
            elif _CITATION.search(question) and has("db_query"):
                return _resp(
                    messages,
                    None,
                    [
                        _call(
                            "db_query",
                            {
                                "query": "SELECT title, citations FROM reports "
                                "ORDER BY published_at DESC"
                            },
                        )
                    ],
                )
            else:
                calls = []
                if _DB.search(question) and has("db_query"):
                    calls.append(
                        _call(
                            "db_query",
                            {
                                "query": "SELECT id, title, topic, author, "
                                "published_at FROM reports ORDER BY "
                                "published_at DESC LIMIT 5"
                            },
                        )
                    )
                if _READ.search(question) and has("read_file"):
                    calls.append(_call("read_file", {"path": "welcome.txt"}))
                if _WEB.search(question) and has("web_search"):
                    calls.append(
                        _call(
                            "web_search",
                            {"query": question[:120], "max_results": 3},
                        )
                    )
                if calls:
                    return _resp(messages, None, calls)
                if _WRITE.search(question) and has("write_file"):
                    requested = next(
                        (
                            t.strip()
                            for t in _BACKTICK.findall(question)
                            if "/" in t and re.fullmatch(r"[\w./-]+\.[a-z0-9]+", t.strip())
                        ),
                        "notes/stub-note.md",
                    )
                    return _resp(
                        messages,
                        None,
                        [
                            _call(
                                "write_file",
                                {
                                    "path": requested,
                                    "content": f"Stub note for: {question[:160]}",
                                },
                            )
                        ],
                    )

        if (
            rounds == 1
            and _CITATION.search(question)
            and _tool_calls_by(messages, "db_query") == 1
            and has("db_query")
        ):
            return _resp(
                messages,
                None,
                [
                    _call(
                        "db_query",
                        {
                            "query": "SELECT title, author, published_at FROM "
                            "reports ORDER BY published_at DESC LIMIT 5"
                        },
                    )
                ],
            )

        if (
            rounds > 0
            and not adversarial
            and _WRITE.search(question)
            and has("write_file")
            and not _already_wrote(messages)
        ):
            # Honor a backticked target path when the question names one
            # (`reports/security-briefing.md`), else the stub's default.
            # A directory separator is required so a read-target filename
            # like `welcome.txt` is never mistaken for the write target.
            requested = next(
                (
                    t.strip()
                    for t in _BACKTICK.findall(question)
                    if "/" in t and re.fullmatch(r"[\w./-]+\.[a-z0-9]+", t.strip())
                ),
                "reports/research-summary.md",
            )
            return _resp(
                messages,
                None,
                [
                    _call(
                        "write_file",
                        {
                            "path": requested,
                            "content": _build_stub_report(messages),
                        },
                    )
                ],
            )

        if adversarial:
            if storm:
                content = (
                    "Stub answer: issued the repeated queries as asked; the rate "
                    "limiter rejected the excess calls — "
                    "the guardrail did its job. (Run with OPENAI_API_KEY set for a "
                    "live model.)"
                )
            elif rounds == 0:
                content = (
                    "Stub answer: declined — the request asks for something the "
                    "safety rules forbid, so no tool calls were made. (Run with "
                    "OPENAI_API_KEY set for a live model.)"
                )
            else:
                content = (
                    "Stub answer: the requested operation was rejected by the "
                    "server's guardrails; nothing unsafe was executed. (Run with "
                    "OPENAI_API_KEY set for a live model.)"
                )
        elif _tool_rounds(messages) == 0:
            content = (
                "Stub answer: a general-knowledge question needs no tools — "
                "answered directly from the model's knowledge. (Run with "
                "OPENAI_API_KEY set for a live model.)"
            )
        else:
            content = (
                "Stub answer: gathered evidence via MCP tools and persisted a "
                "report inside the sandbox when asked to. (Run with "
                "OPENAI_API_KEY set for a live model.)"
            )
        if on_delta is not None:
            for chunk in _delta_chunks(content):
                on_delta(chunk)
        return _resp(messages, content, [])
