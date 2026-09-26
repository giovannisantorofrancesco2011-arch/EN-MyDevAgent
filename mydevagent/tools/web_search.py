"""Web search with a fallback chain: Tavily → Firecrawl → SearXNG → DuckDuckGo."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx

from ..config import WebConfig
from . import tool


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass
class SearchResponse:
    query: str
    provider: str | None = None
    results: list[SearchResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    offline: bool = False


class Provider:
    name = "base"

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    def available(self) -> bool:
        return True

    def search(self, query: str, k: int) -> list[SearchResult]:
        raise NotImplementedError


class Tavily(Provider):
    name = "tavily"

    def available(self) -> bool:
        return bool(os.environ.get("TAVILY_API_KEY"))

    def search(self, query, k):
        resp = httpx.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"},
            json={"query": query, "max_results": k, "search_depth": "basic"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return [
            SearchResult(r.get("title", ""), r.get("url", ""), r.get("content", ""))
            for r in resp.json().get("results", [])
        ]


class Firecrawl(Provider):
    name = "firecrawl"

    def available(self) -> bool:
        return bool(os.environ.get("FIRECRAWL_API_KEY"))

    def search(self, query, k):
        resp = httpx.post(
            "https://api.firecrawl.dev/v1/search",
            headers={"Authorization": f"Bearer {os.environ['FIRECRAWL_API_KEY']}"},
            json={"query": query, "limit": k},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if isinstance(data, dict):  # API v2: {"web": [...]}
            data = data.get("web", [])
        return [
            SearchResult(r.get("title", ""), r.get("url", ""), r.get("description", "") or r.get("markdown", "")[:300])
            for r in data
        ]


class SearXNG(Provider):
    name = "searxng"

    def available(self) -> bool:
        return bool(os.environ.get("SEARXNG_URL"))

    def search(self, query, k):
        base = os.environ["SEARXNG_URL"].rstrip("/")
        resp = httpx.get(f"{base}/search", params={"q": query, "format": "json"}, timeout=self.timeout)
        resp.raise_for_status()
        return [
            SearchResult(r.get("title", ""), r.get("url", ""), r.get("content", ""))
            for r in resp.json().get("results", [])[:k]
        ]


class DuckDuckGo(Provider):
    name = "duckduckgo"

    def _cls(self):
        try:
            from ddgs import DDGS
        except ImportError:
            try:
                from duckduckgo_search import DDGS  # legacy package name
            except ImportError:
                return None
        return DDGS

    def available(self) -> bool:
        return self._cls() is not None

    def search(self, query, k):
        ddgs = self._cls()()
        return [
            SearchResult(r.get("title", ""), r.get("href", ""), r.get("body", ""))
            for r in ddgs.text(query, max_results=k)
        ]


PROVIDERS = {cls.name: cls for cls in (Tavily, Firecrawl, SearXNG, DuckDuckGo)}


class WebSearch:
    def __init__(self, cfg: WebConfig, connectivity) -> None:
        self.cfg = cfg
        self.connectivity = connectivity
        self.providers = [PROVIDERS[name](cfg.timeout_s) for name in cfg.providers if name in PROVIDERS]

    def enabled(self) -> bool:
        return self.cfg.enabled and self.connectivity.online()

    def search(self, query: str, k: int | None = None) -> SearchResponse:
        response = SearchResponse(query=query)
        if not self.cfg.enabled:
            response.errors.append("web search disabled in settings")
            return response
        if not self.connectivity.online():
            response.offline = True
            return response
        k = k or self.cfg.max_results
        for provider in self.providers:
            if not provider.available():
                continue
            try:
                results = [r for r in provider.search(query, k) if r.url]
            except Exception as exc:  # move on to the next provider
                response.errors.append(f"{provider.name}: {type(exc).__name__}: {exc}")
                continue
            if results:
                response.provider = provider.name
                response.results = results[:k]
                return response
        if not any(p.available() for p in self.providers):
            response.errors.append("no search provider configured (set TAVILY_API_KEY or pip install ddgs)")
        return response


def format_results(response: SearchResponse, start: int = 1) -> str:
    if response.offline:
        return "OFFLINE: web search unavailable."
    if not response.results:
        return "NO RESULTS" + (f" ({'; '.join(response.errors)})" if response.errors else "")
    lines = []
    for i, r in enumerate(response.results, start=start):
        snippet = " ".join(r.snippet.split())[:400]
        lines.append(f"[{i}] {r.title} — {r.url}\n    {snippet}")
    return "\n".join(lines)


@tool(
    "web_search",
    "Search the web for up-to-date information (docs, versions, changelogs, CVEs). Returns titles, URLs, snippets.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query, concise and specific"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
    },
)
def web_search_tool(ctx, query: str, max_results: int | None = None) -> str:
    return format_results(ctx.web.search(query, max_results))
