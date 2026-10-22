"""Read-only SQL guard — the first of two defense layers for ``db_query``
(the second is opening the SQLite connection itself read-only).

Rules: a single statement, starting with SELECT or WITH (CTEs are
read-only), no forbidden write/DDL/administrative keywords anywhere,
comments stripped before inspection — plus Unicode hardening on top of
the oracle: the statement is NFKC-normalized and Unicode format chars
(zero-width, bidi overrides) are removed *before* inspection, so
look-alike or smuggled keywords cannot evade the forbidden set.
"""

from __future__ import annotations

import re
import unicodedata

from mcp_toolkit.result import ToolError

_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|pragma|attach|detach|vacuum|"
    r"replace|grant|revoke|truncate|begin|commit|rollback|savepoint|release|"
    r"reindex|analyze|load_extension)\b",
    re.IGNORECASE,
)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_TRAILING_JUNK = re.compile(r"[\s;]+$")
_START = re.compile(r"^(select|with)\b", re.IGNORECASE)


def _strip_comments(sql: str) -> str:
    return _LINE_COMMENT.sub(" ", _BLOCK_COMMENT.sub(" ", sql))


def _inspection_form(cleaned: str) -> str:
    """NFKC-normalize and drop Unicode format characters so that
    fullwidth/homoglyph look-alikes and zero-width smuggling cannot slip
    a forbidden keyword past the word-boundary check."""
    normalized = unicodedata.normalize("NFKC", cleaned)
    return "".join(c for c in normalized if unicodedata.category(c) != "Cf")


def assert_read_only_select(sql: str) -> None:
    """Raise :class:`ToolError` unless ``sql`` is a single read-only SELECT.

    Intentionally strict: a ``;`` inside a string literal or an identifier
    colliding with a denied keyword is also rejected — ``db_query`` is for
    evidence lookups, not arbitrary SQL, so false positives are accepted
    and documented.
    """
    cleaned = _strip_comments(sql).strip()
    cleaned = _TRAILING_JUNK.sub("", cleaned)  # tolerate trailing ;/whitespace
    if cleaned == "":
        raise ToolError("invalid_input", "Empty SQL statement", guardrail="sql_guard")
    inspection = _inspection_form(cleaned)
    if ";" in inspection:
        raise ToolError(
            "forbidden", "Only a single SELECT statement is allowed", guardrail="sql_guard"
        )
    if not _START.match(inspection):
        raise ToolError("forbidden", "Only SELECT statements are allowed", guardrail="sql_guard")
    hit = _FORBIDDEN_KEYWORDS.search(inspection)
    if hit:
        raise ToolError(
            "forbidden",
            f"Statement contains a forbidden keyword: {hit.group(1).upper()}",
            guardrail="sql_guard",
        )
