"""Per-tool output models — the ``outputSchema`` each tool publishes.

Tools still return the JSON text envelope for backwards compatibility;
the same envelope is also emitted as ``structuredContent`` so clients can
consume typed results instead of parsing text. These models describe
exactly what the handlers produce — the SDK validates the emitted
``structuredContent`` against them on every non-error call.

When the character cap truncates the text view mid-JSON there is no
faithful structure to emit: the result becomes a typed ``too_large``
error arm instead (see ``invocation._result``), which is also exempt
from the SDK's schema validation.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class _OkSide(BaseModel):
    """Envelope success arm — ``data`` is typed per tool."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"] = "ok"


class SearchHitOutput(BaseModel):
    """One normalized hit — every provider adapter emits this exact shape."""

    model_config = ConfigDict(extra="forbid")
    title: str
    url: str
    snippet: str


class WebSearchData(BaseModel):
    provider: str
    query: str
    results: list[SearchHitOutput]


class WebSearchOutput(_OkSide):
    data: WebSearchData


class DbQueryData(BaseModel):
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool


class DbQueryOutput(_OkSide):
    data: DbQueryData


class ReadFileData(BaseModel):
    path: str
    bytes: int
    content: str


class ReadFileOutput(_OkSide):
    data: ReadFileData


class WriteFileData(BaseModel):
    path: str
    bytes_written: int


class WriteFileOutput(_OkSide):
    data: WriteFileData
