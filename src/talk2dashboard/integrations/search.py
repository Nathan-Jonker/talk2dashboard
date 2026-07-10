from __future__ import annotations

import time

import httpx

from talk2dashboard.config import Settings
from talk2dashboard.integrations.audit import record_provider_call
from talk2dashboard.storage.database import Database


class BraveSearchClient:
    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, settings: Settings, database: Database | None = None) -> None:
        self.settings = settings
        self.database = database

    async def search(
        self, query: str, *, max_results: int = 5, recency_days: int | None = None
    ) -> list[dict]:
        if not self.settings.brave_search_api_key:
            raise RuntimeError("BRAVE_SEARCH_NOT_CONFIGURED")
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
