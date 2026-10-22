"""Search providers for ``web_search``.

``SEARCH_PROVIDER``: ``auto`` prefers Tavily when keyed, else keyless
DuckDuckGo; ``none`` returns deterministic canned hits so demos, tests
and CI run fully offline. Network calls go through an injectable httpx
client (respx in tests), never hidden inside the tool.
"""

from __future__ import annotations

import urllib.parse
from typing import Protocol, TypedDict

import httpx
from bs4 import BeautifulSoup

from mcp_toolkit.config import Settings
from mcp_toolkit.result import ToolError

SEARCH_TIMEOUT_S = 10.0


class SearchHit(TypedDict):
    title: str
    url: str
    snippet: str


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, max_results: int) -> list[SearchHit]: ...


class TavilyProvider:
    """Paid provider — Tavily REST API, key via Authorization header."""

    name = "tavily"

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=SEARCH_TIMEOUT_S)

    async def search(self, query: str, max_results: int) -> list[SearchHit]:
        try:
            res = await self._client.post(
                "https://api.tavily.com/search",
                headers={"authorization": f"Bearer {self._api_key}"},
                json={"query": query, "max_results": max_results, "search_depth": "basic"},
            )
        except httpx.HTTPError as exc:
            raise ToolError("upstream", f"Search request failed: {exc}") from exc
        if res.status_code != 200:
            raise ToolError("upstream", f"Tavily responded HTTP {res.status_code}")
        body = res.json()
        raw = body.get("results")
        results = raw if isinstance(raw, list) else []
        return [
            SearchHit(
                title=str(r.get("title") or ""),
                url=str(r.get("url") or ""),
                snippet=str(r.get("content") or ""),
            )
            for r in results[:max_results]
            if isinstance(r, dict)
        ]


def _decode_ddg_redirect(href: str) -> str | None:
    """DDG wraps outbound links as ``/l/?uddg=<url-encoded>`` — unwrap."""
    try:
        url = urllib.parse.urlparse(href if "//" in href else f"https://duckduckgo.com{href}")
        uddg = urllib.parse.parse_qs(url.query).get("uddg")
        if uddg:
            return uddg[0]
        return href if href.startswith("http") else None
    except ValueError:
        return None


class DuckDuckGoProvider:
    """Keyless fallback — DuckDuckGo HTML endpoint, no credentials."""

    name = "duckduckgo"

    _UA = (
        "Mozilla/5.0 (compatible; mcp-agent-toolkit/0.1; "
        "+https://github.com/kanderson-ai-dev/mcp-agent-toolkit-py)"
    )

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=SEARCH_TIMEOUT_S)

    async def search(self, query: str, max_results: int) -> list[SearchHit]:
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        try:
            res = await self._client.get(
                url, headers={"user-agent": self._UA, "accept": "text/html"}
            )
        except httpx.HTTPError as exc:
            raise ToolError("upstream", f"Search request failed: {exc}") from exc
        if res.status_code != 200:
            raise ToolError("upstream", f"DuckDuckGo responded HTTP {res.status_code}")

        soup = BeautifulSoup(res.text, "lxml")
        hits: list[SearchHit] = []
        for el in soup.select(".result"):
            a = el.select_one(".result__a")
            href = a.get("href") if a is not None else None
            target = _decode_ddg_redirect(href if isinstance(href, str) else "")
            if a is None or not target:
                continue
            snippet_el = el.select_one(".result__snippet")
            hits.append(
                SearchHit(
                    title=a.get_text().strip(),
                    url=target,
                    snippet=snippet_el.get_text().strip() if snippet_el else "",
                )
            )
            if len(hits) >= max_results:
                break
        return hits


class StubSearchProvider:
    """Offline provider — deterministic canned hits (tests and CI)."""

    name = "none"

    async def search(self, query: str, max_results: int) -> list[SearchHit]:
        canned: list[SearchHit] = [
            {
                "title": f'Stub result for "{query}"',
                "url": "https://example.com/stub-result",
                "snippet": "Offline fixture: deterministic search result used when "
                "SEARCH_PROVIDER=none.",
            },
            {
                "title": "Model Context Protocol — specification",
                "url": "https://modelcontextprotocol.io",
                "snippet": "An open protocol for connecting applications to context and tools.",
            },
        ]
        return canned[:max_results]


def create_search_provider(
    settings: Settings, client: httpx.AsyncClient | None = None
) -> SearchProvider:
    """Provider selection: explicit env wins; ``auto`` prefers Tavily when keyed."""
    match settings.search_provider:
        case "tavily":
            if settings.search_api_key is None:
                raise ToolError("invalid_input", "SEARCH_PROVIDER=tavily requires SEARCH_API_KEY")
            return TavilyProvider(settings.search_api_key.get_secret_value(), client)
        case "duckduckgo":
            return DuckDuckGoProvider(client)
        case "none":
            return StubSearchProvider()
        case "auto":
            return (
                TavilyProvider(settings.search_api_key.get_secret_value(), client)
                if settings.search_api_key is not None
                else DuckDuckGoProvider(client)
            )
