from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from talk2dashboard.domain import EventRecord, SourceRef, TrustTier
from talk2dashboard.sources.base import AdapterResult, SourceAdapter


class RSSAdapter(SourceAdapter):
    def __init__(
        self,
        *,
        stream_id: str,
        owner: str,
        provider: str,
        url: str,
        category: str,
        trust_tier: TrustTier,
        cadence_seconds: int,
    ) -> None:
        self.stream_id = stream_id
        self.owner = owner
        self.provider = provider
        self.url = url
        self.category = category
        self.trust_tier = trust_tier
        self.expected_cadence_seconds = cadence_seconds
        super().__init__()

    async def fetch(self) -> AdapterResult:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(self.url, headers={"User-Agent": "Talk2Dashboard/0.1"})
            response.raise_for_status()
        now = datetime.now(UTC)
        parsed = feedparser.parse(response.content)  # pyright: ignore[reportAttributeAccessIssue]
        events: list[EventRecord] = []
        for entry in parsed.entries[:100]:
            raw_id = str(entry.get("id") or entry.get("link") or entry.get("title"))
            record_id = f"{self.stream_id}:{hashlib.sha256(raw_id.encode()).hexdigest()[:20]}"
            published = entry.get("published") or entry.get("updated")
            try:
                observed_at = parsedate_to_datetime(published).astimezone(UTC) if published else now
            except (TypeError, ValueError):
                observed_at = now
            source_ref = SourceRef(
                stream_id=self.stream_id,
                record_id=record_id,
                source_url=entry.get("link"),
                owner=self.owner,
                trust_tier=self.trust_tier,
                observed_at=observed_at,
                ingested_at=now,
            )
            events.append(
                EventRecord(
                    record_id=record_id,
                    stream_id=self.stream_id,
                    category=self.category,
                    title=str(entry.get("title", "Zonder titel"))[:300],
                    description=str(entry.get("summary", ""))[:1000] or None,
                    observed_at=observed_at,
                    ingested_at=now,
                    source_ref=source_ref,
                    attributes={"link": entry.get("link")},
                )
            )
        return AdapterResult(
            stream_id=self.stream_id,
            provider=self.provider,
            source_url=self.url,
            raw=response.content,
            observed_at=max((event.observed_at for event in events), default=now),
            events=events,
        )
