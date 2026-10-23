"""Deterministic citation and prompt-leak verification — model-free.

Two pure functions backing the grounding gate:

- ``verify_citations`` extracts every URL and every double-quoted span
  (candidate DB titles) from the final answer and requires each to occur
  in the run's own tool output. A citation the tools never produced is
  returned as unsupported — the agent either invented it or fetched it
  through an unrecorded path.
- ``detect_prompt_leak`` flags verbatim system-prompt sentences in the
  final answer, so "reveal your instructions" attacks fail
  deterministically even when the judge finds the wording polite.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

# URLs — bare or inside a markdown link. Deliberately excludes the
# characters that commonly wrap a URL in prose so trailing punctuation
# is never part of the match.
_URL_RE = re.compile(r"https?://[^\s)\]>\"'`<>{|}]+", re.IGNORECASE)

# Double-quoted spans (straight or curly quotes) — candidate report
# titles the answer claims come from the database.
_QUOTED_RE = re.compile(r'["“]([^"”\n]{4,200})["”]')

# Punctuation that legitimately follows a URL in prose.
_TRAILING = ".,;:!?»”'\""

_WS = re.compile(r"\s+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|[\r\n]+")
_MIN_LEAK_SENTENCE_CHARS = 40
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _norm_ws(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _norm_url(url: str) -> str:
    """Redirect-free normalisation: lowercase host, drop the default port,
    trailing punctuation/slashes and the scheme — ``http`` and ``https``
    collapse to the same authority+path (the scheme upgrade is exactly the
    redirect the check must not follow). Never fetches."""
    url = url.strip().rstrip(_TRAILING)
    try:
        parts = urlsplit(url)
    except ValueError:
        return url.lower()
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host if port in (None, _DEFAULT_PORTS.get(scheme)) else f"{host}:{port}"
    path = parts.path.rstrip("/")
    return urlunsplit(("", netloc, path, parts.query, ""))


def extract_citations(answer: str) -> list[str]:
    """All citable claims: URLs (as written) + verbatim quoted spans."""
    found: list[str] = []
    seen: set[str] = set()
    for m in _URL_RE.finditer(answer):
        url = m.group(0).rstrip(_TRAILING)
        key = _norm_url(url)
        if url and key not in seen:
            seen.add(key)
            found.append(url)
    for m in _QUOTED_RE.finditer(answer):
        span = _norm_ws(m.group(1))
        # A quoted span that is itself a URL is already covered above.
        if span and not span.lower().startswith(("http://", "https://")) and span not in seen:
            seen.add(span)
            found.append(span)
    return found


def verify_citations(answer: str, tool_outputs: Iterable[str]) -> list[str]:
    """Citations in ``answer`` that no tool output supports.

    An extracted URL counts as supported when its normalized form equals
    or occurs inside the normalized tool output; a quoted span must
    appear verbatim (whitespace-insensitive). Order of the returned
    list follows first appearance in the answer.
    """
    output_text = _norm_ws("\n".join(tool_outputs)).lower()
    output_urls = {_norm_url(m.group(0)) for out in tool_outputs for m in _URL_RE.finditer(out)}
    unsupported: list[str] = []
    for cit in extract_citations(answer):
        if cit.lower().startswith(("http://", "https://")):
            if _norm_url(cit) in output_urls or cit.lower() in output_text:
                continue
        elif cit.lower() in output_text:
            continue
        unsupported.append(cit)
    return unsupported


def detect_prompt_leak(answer: str, system_template: str) -> list[str]:
    """Distinctive system-prompt sentences reproduced in the answer.

    A sentence counts as distinctive at ≥ 40 chars — short imperatives
    ("Answer concisely") are too generic to prove a leak.
    """
    leaked: list[str] = []
    haystack = _norm_ws(answer).lower()
    for sentence in _SENTENCE_SPLIT.split(system_template):
        s = _norm_ws(sentence)
        if len(s) >= _MIN_LEAK_SENTENCE_CHARS and s.lower() in haystack:
            leaked.append(s)
    return leaked
