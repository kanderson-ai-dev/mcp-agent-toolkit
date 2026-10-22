"""Tool-output sanitization — OWASP LLM01 hygiene on the server side.

Everything a tool returns is *untrusted third-party data*: web results
can carry embedded instructions ("ignore previous instructions…"),
files can hold exploit text, DB rows can be adversarial.

Server-side defense is intentionally conservative — we normalize and cap
rather than rewrite, because evidence integrity matters to the caller.
The *framing* defense (treating content as data, never as instructions)
lives in the client's system prompt + ``tool`` role.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# C0/C1 control chars except \t \n \r (NUL, ESC/ANSI, DEL…), plus
# zero-width and bidi-override format chars (Trojan-Source class). Keeps
# transcripts and logs safe to view. Written as escapes so this source
# file stays plain ASCII.
_CONTROL_CHARS = re.compile(
    "[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f"
    "\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]"
)


@dataclass(frozen=True)
class SanitizedText:
    text: str
    truncated: bool


def sanitize_tool_output(text: str, max_chars: int) -> SanitizedText:
    """Strip hostile characters, then cap with an explicit marker."""
    cleaned = _CONTROL_CHARS.sub("", text)
    if len(cleaned) <= max_chars:
        return SanitizedText(text=cleaned, truncated=False)
    return SanitizedText(
        text=f"{cleaned[:max_chars]}\n[truncated: tool output exceeded {max_chars} chars]",
        truncated=True,
    )
