import asyncio
import logging

import httpx
from pydantic import BaseModel

from config import get_settings

logger = logging.getLogger(__name__)


class WebSearchResult(BaseModel):
    title: str
    url: str
    content: str
    score: float
    published_date: str | None = None
    source_engine: str = "searxng"


_SHARED_CLIENT: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """Return a shared httpx.AsyncClient with keep-alive connection pooling."""
    global _SHARED_CLIENT
    if _SHARED_CLIENT is None or _SHARED_CLIENT.is_closed:
        _SHARED_CLIENT = httpx.AsyncClient(
            limits=httpx.Limits(max_keepalive_connections=50, max_connections=100, keepalive_expiry=60.0),
            timeout=httpx.Timeout(10.0, connect=3.0),
            follow_redirects=True,
        )
    return _SHARED_CLIENT


class WebSearchTool:
    """Tool for performing web searches using SearXNG (primary) with DuckDuckGo fallback.

    Ensures internet search is always available:
    1. Primary: SearXNG metasearch instance (if configured via SEARXNG_URL).
    2. Fallback: DuckDuckGo (free, zero API keys required).
    """

    def __init__(self, searxng_url: str | None = None, api_key: str | None = None):
        self.settings = get_settings()
        self.searxng_url = searxng_url or self.settings.searxng_url
        self.api_key = api_key or self.settings.tavily_api_key

    async def search(
        self, query: str, max_results: int = 5, domains: list[str] | None = None
    ) -> list[WebSearchResult]:
        """Perform a web search. Uses SearXNG if available, falls back to DuckDuckGo."""
        from core.tool_cache import get_cached_tool_result, set_cached_tool_result
        params = {"query": query, "max_results": max_results, "domains": domains or []}
        cached = await get_cached_tool_result("web_search", params)
        if cached:
            return [WebSearchResult(**item) for item in cached]

        results: list[WebSearchResult] = []
        if self.searxng_url:
            results = await self._searxng_search(query, max_results, domains)
            if not results and self.settings.duckduckgo_fallback:
                results = await self._duckduckgo_search(query, max_results)
        elif self.api_key:
            results = await self._tavily_search(query, max_results, domains)
            if not results and self.settings.duckduckgo_fallback:
                results = await self._duckduckgo_search(query, max_results)
        elif self.settings.duckduckgo_fallback:
            results = await self._duckduckgo_search(query, max_results)
        else:
            logger.warning("No web search provider available. Set SEARXNG_URL or enable DUCKDUCKGO_FALLBACK.")

        if results:
            await set_cached_tool_result("web_search", params, [r.model_dump() for r in results])

        return results

    async def _searxng_search(
        self, query: str, max_results: int = 5, domains: list[str] | None = None
    ) -> list[WebSearchResult]:
        """Perform a metasearch using a SearXNG instance with connection pooling."""
        if not self.searxng_url:
            return []

        search_query = query
        if domains:
            site_filter = " OR ".join(f"site:{d}" for d in domains)
            search_query = f"{query} ({site_filter})"

        url = f"{self.searxng_url.rstrip('/')}/search"
        params = {
            "q": search_query,
            "format": "json",
            "categories": "general",
        }

        timeout = float(getattr(self.settings, "web_search_timeout_seconds", 5.0))
        try:
            client = _get_http_client()
            response = await client.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            data = response.json()

            results: list[WebSearchResult] = []
            for i, item in enumerate(data.get("results", [])[:max_results]):
                score_val = item.get("score")
                try:
                    score = float(score_val) if score_val is not None else max(0.1, 1.0 - (i * 0.1))
                except (ValueError, TypeError):
                    score = max(0.1, 1.0 - (i * 0.1))

                engine_name = item.get("engine", "searxng")
                results.append(
                    WebSearchResult(
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        content=item.get("content", "") or item.get("snippet", ""),
                        score=round(score, 2),
                        published_date=item.get("publishedDate"),
                        source_engine=f"searxng:{engine_name}" if engine_name != "searxng" else "searxng",
                    )
                )
            logger.info(f"SearXNG returned {len(results)} results for: {query[:80]}")
            return results
        except Exception as e:
            logger.error(f"SearXNG search failed: {e}")
            return []

    async def _tavily_search(
        self, query: str, max_results: int = 5, domains: list[str] | None = None
    ) -> list[WebSearchResult]:
        """Perform a web search using Tavily API (legacy fallback)."""
        url = "https://api.tavily.com/search"
        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "advanced",
            "include_domains": domains or [],
        }

        try:
            client = _get_http_client()
            response = await client.post(url, json=payload, timeout=15.0)
            response.raise_for_status()
            data = response.json()

            results = []
            for res in data.get("results", []):
                results.append(
                    WebSearchResult(
                        title=res.get("title", ""),
                        url=res.get("url", ""),
                        content=res.get("content", ""),
                        score=res.get("score", 0.0),
                        published_date=res.get("published_date"),
                        source_engine="tavily",
                    )
                )
            return results
        except Exception as e:
            logger.error(f"Tavily search failed: {e}")
            return []

    async def _duckduckgo_search(
        self, query: str, max_results: int = 5
    ) -> list[WebSearchResult]:
        """Fallback web search using DuckDuckGo (free, no API key required)."""
        try:
            from duckduckgo_search import DDGS

            ddgs = DDGS()
            raw_results = await asyncio.to_thread(ddgs.text, query, max_results=max_results)

            results = []
            for i, res in enumerate(raw_results):
                results.append(
                    WebSearchResult(
                        title=res.get("title", ""),
                        url=res.get("href", res.get("link", "")),
                        content=res.get("body", res.get("snippet", "")),
                        score=round(1.0 - (i * 0.1), 2),
                        source_engine="duckduckgo",
                    )
                )
            logger.info(f"DuckDuckGo returned {len(results)} results for: {query[:80]}")
            return results
        except ImportError:
            logger.error("duckduckgo-search package not installed. Run: pip install duckduckgo-search")
            return []
        except Exception as e:
            logger.error(f"DuckDuckGo search failed: {e}")
            return []

    async def search_for_problem_solving(
        self, query: str, max_results: int = 5
    ) -> list[WebSearchResult]:
        """Enhanced search specifically for troubleshooting and problem-solving.

        Formulates multiple targeted searches to find solutions, workarounds,
        and official fixes for user issues, executed concurrently.
        """
        all_results: list[WebSearchResult] = []
        seen_urls: set[str] = set()

        # Search variations for better problem-solving coverage executed concurrently
        search_queries = [
            query,
            f"{query} solution fix",
            f"{query} troubleshooting guide",
        ]

        batch_results = await asyncio.gather(
            *[self.search(query=sq, max_results=max_results) for sq in search_queries],
            return_exceptions=True,
        )

        for res_list in batch_results:
            if isinstance(res_list, BaseException) or not res_list:
                continue
            for r in res_list:
                if r.url not in seen_urls:
                    seen_urls.add(r.url)
                    all_results.append(r)

        # Sort by score and limit
        all_results.sort(key=lambda x: x.score, reverse=True)
        return all_results[:max_results * 2]  # Return up to 2x results for problem-solving

    async def search_cloud_updates(self, provider: str) -> list[WebSearchResult]:
        """Targeted search for recent updates from a specific provider."""
        domains_map = {
            "aws": ["aws.amazon.com/about-aws/whats-new"],
            "gcp": ["cloud.google.com/release-notes"],
            "azure": ["azure.microsoft.com/en-us/updates"],
        }

        domains = domains_map.get(provider.lower(), [])
        query = f"latest {provider} updates announcements releases"

        return await self.search(query=query, max_results=5, domains=domains)
