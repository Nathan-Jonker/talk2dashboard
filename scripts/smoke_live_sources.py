from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from talk2dashboard.domain import TrustTier  # noqa: E402
from talk2dashboard.sources.live import (  # noqa: E402
    LuchtmeetnetAdapter,
    NDWIncidentAdapter,
    RWSWaterAdapter,
)
from talk2dashboard.sources.rss import RSSAdapter  # noqa: E402


async def main() -> int:
    adapters = [
        RWSWaterAdapter(),
        NDWIncidentAdapter(),
        LuchtmeetnetAdapter(),
        RSSAdapter(
            stream_id="nos_rss",
            owner="NOS",
            provider="NOS RSS",
            url="https://feeds.nos.nl/nosnieuwsalgemeen",
            category="media_context",
            trust_tier=TrustTier.MEDIA_CONTEXT,
            cadence_seconds=300,
        ),
    ]
    failed = 0
    for adapter in adapters:
        try:
            result = await adapter.fetch()
            print(
                f"{adapter.stream_id}: {len(result.events)} events, "
                f"{len(result.measurements)} measurements via {result.provider}"
            )
        except Exception as exc:
            failed += 1
            print(f"{adapter.stream_id}: ERROR {type(exc).__name__}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
