"""SQL-guard tests — ported from the oracle's sql-guard.test.ts plus the
Unicode-smuggling and hypothesis mutation coverage the plan requires."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mcp_toolkit.result import ToolError
from mcp_toolkit.server.guardrails.sql_guard import assert_read_only_select

ACCEPT = [
    "SELECT 1",
    "  select id, title from reports  ",
    "SELECT * FROM reports;",
    "SELECT * FROM reports;; ;",
    "WITH recent AS (SELECT * FROM reports) SELECT title FROM recent",
    "SELECT count(*) AS n -- count them\nFROM reports",
    "/* preface */ SELECT title FROM reports",
]

REJECT = [
    "DROP TABLE reports",
    "DELETE FROM reports WHERE id = 1",
    "UPDATE reports SET title = 'x'",
    "INSERT INTO reports (title) VALUES ('x')",
    "REPLACE INTO reports (title) VALUES ('x')",
    "SELECT 1; DROP TABLE reports",
    "SELECT * FROM reports; SELECT * FROM reports",
    "PRAGMA table_info(reports)",
    "ATTACH DATABASE 'evil.db' AS e",
    "VACUUM",
    "BEGIN TRANSACTION",
    "SELECT load_extension('x')",
    "CREATE TABLE x (a)",
    "ALTER TABLE reports ADD COLUMN x TEXT",
]

WRITE_STATEMENTS = [
    "DROP TABLE reports",
    "DELETE FROM reports",
    "UPDATE reports SET x = 1",
    "INSERT INTO reports VALUES (1)",
    "PRAGMA writable_schema = 1",
    "ATTACH DATABASE 'x' AS e",
]

ZERO_WIDTH = ["\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"]


class TestAcceptReject:
    @pytest.mark.parametrize("sql", ACCEPT)
    def test_accepts_read_only(self, sql: str) -> None:
        assert_read_only_select(sql)

    @pytest.mark.parametrize("sql", REJECT)
    def test_rejects_non_select_or_multi(self, sql: str) -> None:
        with pytest.raises(ToolError) as exc:
            assert_read_only_select(sql)
        assert exc.value.code == "forbidden"

    def test_empty_statement_is_invalid_input(self) -> None:
        with pytest.raises(ToolError) as exc:
            assert_read_only_select("   \n  ")
        assert exc.value.code == "invalid_input"

    def test_comment_smuggling_cannot_bypass(self) -> None:
        with pytest.raises(ToolError):
            assert_read_only_select("SEL/**/ECT * FROM reports")
        with pytest.raises(ToolError):
            assert_read_only_select("-- sneaky\nDROP TABLE reports")


class TestUnicodeSmuggling:
    """Plan addition on top of the oracle: look-alikes and zero-width
    characters inside keywords are rejected after NFKC normalisation."""

    @pytest.mark.parametrize("zw", ZERO_WIDTH)
    def test_zero_width_inside_keyword_rejected(self, zw: str) -> None:
        with pytest.raises(ToolError):
            assert_read_only_select(f"DR{zw}OP TABLE reports")

    def test_fullwidth_letters_cannot_smuggle_writes(self) -> None:
        fullwidth_drop = "".join(chr(ord(c) + 0xFEE0) for c in "DROP")
        with pytest.raises(ToolError):
            assert_read_only_select(f"{fullwidth_drop} TABLE reports")

    def test_cyrillic_lookalike_select_rejected(self) -> None:
        with pytest.raises(ToolError):
            assert_read_only_select("ѕelect * from reports")  # Cyrillic 'ѕ'

    def test_bidi_override_inside_statement_rejected(self) -> None:
        with pytest.raises(ToolError):
            assert_read_only_select("DR\u202eOP TABLE reports")

    @given(
        st.data(),
        st.sampled_from(WRITE_STATEMENTS),
    )
    def test_no_mutated_write_statement_ever_passes(self, data: st.DataObject, sql: str) -> None:
        """Hypothesis: random zero-width insertion and fullwidth
        substitution inside a write statement must still fail."""
        mutated_chars = []
        for ch in sql:
            if ch.isalpha() and data.draw(st.booleans()):
                mutated_chars.append(chr(ord(ch) + 0xFEE0) if ch.isascii() else ch)
            else:
                mutated_chars.append(ch)
            if data.draw(st.integers(0, 10)) == 0:
                mutated_chars.append(data.draw(st.sampled_from(ZERO_WIDTH)))
        with pytest.raises(ToolError):
            assert_read_only_select("".join(mutated_chars))
