from __future__ import annotations

import asyncio
import time

import feedparser  # pyright: ignore[reportMissingImports]
import httpx
from ddgs import DDGS

from talk2dashboard.config import Settings
from talk2dashboard.integrations.audit import record_provider_call
from talk2dashboard.storage.database import Database


class BraveSearchClient:
    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
    GOOGLE_NEWS_ENDPOINT = "https://news.google.com/rss/search"

    def __init__(self, settings: Settings, database: Database | None = None) -> None:
        self.settings = settings
        self.database = database

    async def search(
        self, query: str, *, max_results: int = 5, recency_days: int | None = None
    ) -> list[dict]:
        if not self.settings.brave_search_api_key:
            try:
                return await self._duckduckgo_search(
                    query, max_results=max_results, recency_days=recency_days
                )
            except Exception:
                return await self._google_news_search(
                    query, max_results=max_results, recency_days=recency_days
                )
        params: dict[str, str | int] = {
            "q": query,
            "count": min(max_results, 5),
            "country": "nl",
            "search_lang": "nl",
        }
        if recency_days:
            params["freshness"] = f"{recency_days}d"
        started = time.monotonic_ns()
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    self.ENDPOINT,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": self.settings.brave_search_api_key,
                    },
                )
                response.raise_for_status()
                data = response.json()
            record_provider_call(
                self.database,
                provider="brave",
                operation="web_search",
                status="success",
                duration_ms=(time.monotonic_ns() - started) / 1_000_000,
                usage={"result_count": len(data.get("web", {}).get("results", []))},
            )
        except Exception:
            record_provider_call(
                self.database,
                provider="brave",
                operation="web_search",
                status="error",
                duration_ms=(time.monotonic_ns() - started) / 1_000_000,
            )
            raise
        return [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("description"),
                "provider": "Brave Search",
            }
            for item in data.get("web", {}).get("results", [])[:max_results]
        ]

    async def _duckduckgo_search(
        self, query: str, *, max_results: int, recency_days: int | None
    ) -> list[dict]:
        timelimit = None
        if recency_days:
            timelimit = (
                "d"
                if recency_days <= 1
                else "w"
                if recency_days <= 7
                else "m"
                if recency_days <= 31
                else "y"
            )
        search_options: dict[str, str | int] = {
            "region": "nl-nl",
            "safesearch": "moderate",
            "max_results": min(max_results, 5),
        }
        if timelimit:
            search_options["timelimit"] = timelimit

        started = time.monotonic_ns()
        try:
            raw_results = await asyncio.to_thread(
                lambda: list(DDGS().text(query, **search_options))
            )
            results = [
                {
                    "title": item.get("title"),
                    "url": item.get("href"),
                    "snippet": item.get("body"),
                    "provider": "DuckDuckGo",
                }
                for item in raw_results[:max_results]
            ]
            record_provider_call(
                self.database,
                provider="duckduckgo",
                operation="web_search",
                status="success",
                duration_ms=(time.monotonic_ns() - started) / 1_000_000,
                usage={"result_count": len(results)},
            )
            return results
        except Exception:
            record_provider_call(
                self.database,
                provider="duckduckgo",
                operation="web_search",
                status="error",
                duration_ms=(time.monotonic_ns() - started) / 1_000_000,
            )
            raise

    async def _google_news_search(
        self, query: str, *, max_results: int, recency_days: int | None
    ) -> list[dict]:
        search_query = f"{query} when:{max(1, min(recency_days, 30))}d" if recency_days else query
        started = time.monotonic_ns()
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                response = await client.get(
                    self.GOOGLE_NEWS_ENDPOINT,
                    params={"q": search_query, "hl": "nl", "gl": "NL", "ceid": "NL:nl"},
                )
                response.raise_for_status()
            feed = feedparser.parse(response.content)
            results = [
                {
                    "title": entry.get("title"),
                    "url": entry.get("link"),
                    "snippet": " · ".join(
                        value
                        for value in (
                            (entry.get("source") or {}).get("title"),
                            entry.get("published"),
                        )
                        if value
                    ),
                    "provider": "Google News RSS",
                }
                for entry in feed.entries[:max_results]
            ]
            record_provider_call(
                self.database,
                provider="google_news_rss",
                operation="news_search",
                status="success",
                duration_ms=(time.monotonic_ns() - started) / 1_000_000,
                usage={"result_count": len(results)},
            )
            return results
        except Exception:
            record_provider_call(
                self.database,
                provider="google_news_rss",
                operation="news_search",
                status="error",
                duration_ms=(time.monotonic_ns() - started) / 1_000_000,
            )
            raise
