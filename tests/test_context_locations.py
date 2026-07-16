from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from talk2dashboard.context_locations import ContextLocationService, _location_query
from talk2dashboard.locations import EphemeralLocationStore


class FakeEvidence:
    def __init__(self, record: dict) -> None:
        self.record = record

    def get(self, _source_ref: str) -> dict:
        return {"record": self.record}


def test_p2000_location_query_uses_address_instead_of_dispatch_code() -> None:
    query = _location_query(
        "p2000:bedum",
        {
            "title": "p 1 bnn-01 br buiten kuifeend bedum 011631",
            "description": "Buitenbrand op Kuifeend in Bedum",
        },
    )

    assert query == "Kuifeend in Bedum, Nederland"


@pytest.mark.asyncio
async def test_missing_p2000_coordinates_are_geocoded_once_and_reused(services) -> None:
    _settings, database, _sources, _query, _dashboard = services
    geocoding = AsyncMock()
    geocoding.resolve.return_value = {
        "matches": [
            {
                "place_id": "bedum-place",
                "display_label": "Kuifeend, Bedum",
                "location": {"lat": 53.3001, "lng": 6.6021},
            }
        ]
    }
    service = ContextLocationService(
        FakeEvidence(
            {
                "title": "p 1 bnn-01 br buiten kuifeend bedum 011631",
                "description": "Buitenbrand op Kuifeend in Bedum",
                "location": None,
            }
        ),  # type: ignore[arg-type]
        geocoding,
        EphemeralLocationStore(database),
    )

    first = await service.resolve("p2000:bedum")
    second = await service.resolve("p2000:bedum")

    assert first["location_source"] == "geocoded"
    assert first["label"] == "Kuifeend, Bedum"
    assert first["resolution_id"] == second["resolution_id"]
    geocoding.resolve.assert_awaited_once()
    assert geocoding.resolve.await_args.args == ("Kuifeend in Bedum, Nederland",)
