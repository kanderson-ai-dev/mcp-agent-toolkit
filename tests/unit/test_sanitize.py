"""Tool-output sanitization tests — ported from the oracle's
sanitize.test.ts plus boundary coverage."""

from __future__ import annotations

from mcp_toolkit.server.guardrails.sanitize import sanitize_tool_output


def test_strips_control_chars_but_keeps_whitespace_and_text() -> None:
    dirty = (
        "line1\n"
        + "\x00"  # NUL
        + "\x1b"  # ESC (ANSI)
        + "\x07"  # BEL
        + "evil text"
        + "\u202e"  # bidi override
        + "\u200b"  # zero-width space
    )
    result = sanitize_tool_output(dirty, 10_000)
    assert result.truncated is False
    assert "line1" in result.text
    assert "evil text" in result.text  # instruction text stays — it's data
    assert "\x00" not in result.text
    assert "\x1b" not in result.text
    assert "\x07" not in result.text
    assert "\u202e" not in result.text
    assert "\u200b" not in result.text
    assert "\n" in result.text


def test_ansi_sequences_are_neutralized() -> None:
    dirty = "safe\x1b[2J\x1b[31mred"
    result = sanitize_tool_output(dirty, 10_000)
    assert "\x1b" not in result.text
    assert "safe" in result.text and "red" in result.text


def test_truncates_oversized_output_with_marker() -> None:
    result = sanitize_tool_output("x" * 50_000, 1_000)
    assert result.truncated is True
    assert len(result.text) < 1_100
    assert "truncated" in result.text


def test_exactly_at_cap_is_not_truncated() -> None:
    result = sanitize_tool_output("y" * 100, 100)
    assert result.truncated is False
    assert result.text == "y" * 100


def test_one_over_cap_is_truncated() -> None:
    result = sanitize_tool_output("y" * 101, 100)
    assert result.truncated is True
    assert result.text.startswith("y" * 100)


def test_clean_short_text_passes_untouched() -> None:
    result = sanitize_tool_output("plain\nwith\nlines\n", 100)
    assert result.truncated is False
    assert result.text == "plain\nwith\nlines\n"
