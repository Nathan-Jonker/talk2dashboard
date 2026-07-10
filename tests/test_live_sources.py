from __future__ import annotations

import gzip
import io
from datetime import UTC, datetime

import httpx
import numpy as np
import pytest
import respx
import xarray as xr

from talk2dashboard.domain import TrustTier
from talk2dashboard.incidents import cluster_events
from talk2dashboard.sources.base import AdapterResult, FallbackAdapter, SourceAdapter
from talk2dashboard.sources.fixture import FixtureAdapter
from talk2dashboard.sources.knmi import KNMIObservationsAdapter
from talk2dashboard.sources.live import (
    LuchtmeetnetAdapter,
    NDWIncidentAdapter,
    NSDisruptionsAdapter,
    Radar112Adapter,
    RWSWaterAdapter,
)
from talk2dashboard.sources.rss import RSSAdapter


def test_ndw_datex_parser_extracts_event_and_geometry():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <d2LogicalModel xmlns="http://datex2.eu/schema/3/common" xmlns:sit="http://datex2.eu/schema/3/situation">
      <sit:situationRecord id="record-1">
        <sit:situationRecordCreationTime>2026-07-10T10:00:00Z</sit:situationRecordCreationTime>
        <sit:description><value>Ongeval op de A12</value></sit:description>
        <sit:locationReference><latitude>52.0800</latitude><longitude>5.1200</longitude></sit:locationReference>
      </sit:situationRecord>
    </d2LogicalModel>"""
    events = NDWIncidentAdapter().parse(gzip.compress(xml), datetime(2026, 7, 10, tzinfo=UTC))
    assert len(events) == 1
    assert events[0].title == "Ongeval op de A12"
    assert events[0].location is not None
    assert events[0].location.latitude == 52.08


@respx.mock
async def test_rws_water_contract():
    respx.get(RWSWaterAdapter.url).mock(
        return_value=httpx.Response(
            200,
            json={
                "features": [
                    {
                        "id": "water.1",
                        "geometry": {"coordinates": [4.62, 51.70]},
                        "properties": {
                            "NAAM": "Moerdijk",
                            "WAARDE_LAATSTE_METING": 123.4,
                            "TIJDSTIP_LAATSTE_METING": "2026-07-10T10:00:00Z",
                            "EENHEIDCODE": "cm",
                            "KWALITEITSOORDEELCODE": "00",
                        },
                    }
                ]
            },
        )
    )
    result = await RWSWaterAdapter().fetch()
    assert result.measurements[0].metric == "water_level_cm"
    assert result.measurements[0].value == 123.4
    assert result.measurements[0].source_ref.owner == "Rijkswaterstaat"


@respx.mock
async def test_luchtmeetnet_contract():
    respx.get(f"{LuchtmeetnetAdapter.base_url}/stations").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "number": "NL001",
                        "location": "Utrecht",
                        "geometry": {"coordinates": [5.12, 52.09]},
                    }
                ]
            },
        )
    )
    respx.get(f"{LuchtmeetnetAdapter.base_url}/measurements").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "station_number": "NL001",
                        "formula": "NO2",
                        "value": 18.2,
                        "timestamp_measured": "2026-07-10T10:00:00Z",
                    }
                ]
            },
        )
    )
    result = await LuchtmeetnetAdapter().fetch()
    assert result.measurements[0].metric == "no2_ug_m3"
    assert result.measurements[0].location is not None
    assert result.measurements[0].location.label == "Utrecht"


class _Adapter(SourceAdapter):
    stream_id = "p2000"
    owner = "test"
    expected_cadence_seconds = 60

    def __init__(self, provider: str, fail: bool) -> None:
        self.provider = provider
        self.fail = fail
        super().__init__()

    async def fetch(self) -> AdapterResult:
        if self.fail:
            raise RuntimeError("offline")
        return AdapterResult(
            stream_id=self.stream_id,
            provider=self.provider,
            source_url=None,
            raw=b"ok",
            observed_at=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_p2000_fallback_marks_provider():
    adapter = FallbackAdapter(_Adapter("primary", True), _Adapter("fallback", False))
    result = await adapter.fetch()
    assert result.provider == "fallback"
    assert result.metadata["fallback_from"] == "primary"


async def test_cross_source_clustering_is_deterministic(services):
    settings, *_ = services
    fixture = await FixtureAdapter(settings.fixture_dir / "operational_snapshot.json").fetch()
    first = cluster_events(fixture.events, distance_m=10000, window_minutes=45, active_min_events=2)
    second = cluster_events(
        list(reversed(fixture.events)), distance_m=10000, window_minutes=45, active_min_events=2
    )
    assert [item.model_dump() for item in first] == [item.model_dump() for item in second]
    assert first[0].status == "active"


@respx.mock
async def test_ns_disruptions_uses_subscription_contract() -> None:
    route = respx.get(NSDisruptionsAdapter.url).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "ns-1",
                    "title": "Seinstoring Utrecht",
                    "type": "DISRUPTION",
                    "start": "2026-07-10T10:00:00Z",
                    "isActive": True,
                }
            ],
        )
    )
    result = await NSDisruptionsAdapter("test-subscription").fetch()
    assert result.events[0].title == "Seinstoring Utrecht"
    assert route.calls[0].request.headers["Ocp-Apim-Subscription-Key"] == "test-subscription"
    assert route.calls[0].request.url.params["isActive"] == "true"


@respx.mock
async def test_radar112_uses_bearer_contract() -> None:
    route = respx.get(Radar112Adapter.url).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "p-1",
                        "message": "Brandmelding",
                        "timestamp": "2026-07-10T10:00:00Z",
                        "latitude": 51.7,
                        "longitude": 4.6,
                    }
                ]
            },
        )
    )
    result = await Radar112Adapter("test-token").fetch()
    assert result.events[0].location is not None
    assert route.calls[0].request.headers["Authorization"] == "Bearer test-token"


@respx.mock
async def test_nos_rss_keeps_media_context_trust_tier() -> None:
    url = "https://feeds.example.invalid/nos.xml"
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            content=b"""<?xml version='1.0'?><rss version='2.0'><channel><title>NOS</title>
            <item><guid>nos-1</guid><title>Nieuwscontext</title><link>https://nos.nl/1</link>
            <pubDate>Thu, 10 Jul 2026 10:00:00 GMT</pubDate></item></channel></rss>""",
        )
    )
    adapter = RSSAdapter(
        stream_id="nos_rss",
        owner="NOS",
        provider="NOS RSS",
        url=url,
        category="media_context",
        trust_tier=TrustTier.MEDIA_CONTEXT,
        cadence_seconds=300,
    )
    result = await adapter.fetch()
    assert result.events[0].source_ref.trust_tier == TrustTier.MEDIA_CONTEXT


def test_knmi_netcdf_parser_maps_station_and_metric() -> None:
    dataset = xr.Dataset(
        data_vars={
            "wind_speed_of_gust": (("time", "station"), np.array([[12.5]])),
            "latitude": (("station",), np.array([52.1])),
            "longitude": (("station",), np.array([5.2])),
        },
        coords={
            "time": np.array([np.datetime64("2026-07-10T10:00:00")]),
            "station": np.array(["260"]),
        },
    )
    buffer = io.BytesIO()
    dataset.to_netcdf(buffer, engine="h5netcdf")
    rows = KNMIObservationsAdapter("test", {"260"}).parse(
        buffer.getvalue(), source_url="https://api.dataplatform.knmi.nl/test"
    )
    assert rows[0].metric == "wind_gust_ms"
    assert rows[0].value == 12.5
    assert rows[0].location is not None
    assert rows[0].location.location_id == "knmi:260"
