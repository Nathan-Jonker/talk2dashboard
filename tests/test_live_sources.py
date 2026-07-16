from __future__ import annotations

import asyncio
import gzip
import io
from datetime import UTC, datetime, timedelta

import httpx
import numpy as np
import pytest
import respx
import xarray as xr

from talk2dashboard.domain import SourceHealthStatus, TrustTier
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
from talk2dashboard.sources.service import SourceService


def test_empty_source_result_is_not_reported_as_healthy() -> None:
    adapter = KNMIObservationsAdapter("test")
    adapter.mark_success(
        AdapterResult(
            stream_id=adapter.stream_id,
            provider=adapter.provider,
            source_url="https://api.dataplatform.knmi.nl/test",
            raw=b"empty",
            observed_at=datetime(2026, 7, 10, tzinfo=UTC),
        )
    )

    assert adapter.health.status == SourceHealthStatus.DEGRADED
    assert adapter.health.record_count == 0
    assert adapter.health.message == "Geen bruikbare records ontvangen"


def test_event_feed_freshness_uses_successful_poll_time() -> None:
    adapter = RSSAdapter(
        stream_id="nos_rss",
        owner="NOS",
        provider="NOS RSS",
        url="https://feeds.example.invalid/nos.xml",
        category="media_context",
        trust_tier=TrustTier.MEDIA_CONTEXT,
        cadence_seconds=300,
    )
    now = datetime.now(UTC)
    adapter.health = adapter.health.model_copy(
        update={
            "status": SourceHealthStatus.HEALTHY,
            "last_success_at": now - timedelta(seconds=30),
            "newest_record_at": now - timedelta(hours=3),
            "record_count": 20,
        }
    )
    service = SourceService.__new__(SourceService)
    service.adapters = [adapter]  # pyright: ignore[reportAttributeAccessIssue]
    service._fixture_controls = {}

    health = service.health(now=now)[0]

    assert health.status == SourceHealthStatus.HEALTHY
    assert health.age_seconds == pytest.approx(30)


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


def test_ndw_datex_parser_humanizes_provider_codes() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <d2LogicalModel xmlns="http://datex2.eu/schema/3/common"
      xmlns:sit="http://datex2.eu/schema/3/situation"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <sit:situationRecord id="record-2" xsi:type="sit:SpeedManagement">
        <sit:situationRecordCreationTime>2026-07-10T10:00:00Z</sit:situationRecordCreationTime>
        <sit:sourceName><value>NDW</value></sit:sourceName>
        <sit:cause><sit:causeType>roadMaintenance</sit:causeType></sit:cause>
        <sit:carriageway>mainCarriageway</sit:carriageway>
        <sit:speedManagementType>speedRestrictionInOperation</sit:speedManagementType>
        <sit:temporarySpeedLimit>70.0</sit:temporarySpeedLimit>
        <sit:locationReference><latitude>52.0800</latitude><longitude>5.1200</longitude></sit:locationReference>
      </sit:situationRecord>
    </d2LogicalModel>"""

    event = NDWIncidentAdapter().parse(gzip.compress(xml), datetime(2026, 7, 10, tzinfo=UTC))[0]

    assert event.title == "Tijdelijke snelheidsbeperking"
    assert event.category == "SpeedManagement"
    assert event.description is not None
    assert "wegwerkzaamheden" in event.description
    assert "70 kilometer per uur" in event.description


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


@respx.mock
async def test_luchtmeetnet_enriches_measurements_from_station_detail():
    respx.get(f"{LuchtmeetnetAdapter.base_url}/stations").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"number": "NL10741", "location": "Nijmegen-Graafseweg"}]},
        )
    )
    respx.get(f"{LuchtmeetnetAdapter.base_url}/measurements").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "station_number": "NL10741",
                        "formula": "NO2",
                        "value": 17.8,
                        "timestamp_measured": "2026-07-10T10:00:00Z",
                    }
                ]
            },
        )
    )
    respx.get(f"{LuchtmeetnetAdapter.base_url}/stations/NL10741").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "location": "Nijmegen-Graafseweg",
                    "geometry": {"coordinates": [5.857777, 51.841372]},
                }
            },
        )
    )

    result = await LuchtmeetnetAdapter().fetch()

    assert result.measurements[0].location is not None
    assert result.measurements[0].location.label == "Nijmegen-Graafseweg"
    assert result.measurements[0].location.latitude == 51.841372
    assert result.metadata["stations_with_coordinates"] == 1


@respx.mock
async def test_rws_discards_non_current_locations_from_latest_snapshot():
    respx.get(RWSWaterAdapter.url).mock(
        return_value=httpx.Response(
            200,
            json={
                "features": [
                    {
                        "id": "current",
                        "geometry": {"coordinates": [5.0, 52.0]},
                        "properties": {
                            "NAAM": "Actueel meetpunt",
                            "WAARDE_LAATSTE_METING": 120,
                            "TIJDSTIP_LAATSTE_METING": "2026-07-10T10:00:00Z",
                            "EENHEIDCODE": "cm",
                            "KWALITEITSOORDEELCODE": "00",
                        },
                    },
                    {
                        "id": "historic",
                        "geometry": {"coordinates": [5.1, 52.1]},
                        "properties": {
                            "NAAM": "Historisch meetpunt",
                            "WAARDE_LAATSTE_METING": 125,
                            "TIJDSTIP_LAATSTE_METING": "1985-01-01T00:00:00Z",
                            "EENHEIDCODE": "cm",
                            "KWALITEITSOORDEELCODE": "00",
                        },
                    },
                ]
            },
        )
    )

    result = await RWSWaterAdapter().fetch()

    assert [row.location.label for row in result.measurements if row.location] == [
        "Actueel meetpunt"
    ]
    assert result.metadata["discarded_non_current_records"] == 1


async def test_source_refresh_backs_off_after_provider_rate_limit(services) -> None:
    _settings, _database, sources, _query, _dashboard = services
    adapter = KNMIObservationsAdapter("test")
    sources.adapters = [adapter]
    request = httpx.Request("GET", "https://api.dataplatform.knmi.nl/open-data/v1/files")
    response = httpx.Response(429, request=request, json={"error": "Rate limit exceeded"})

    async def rate_limited():
        raise httpx.HTTPStatusError("rate limited", request=request, response=response)

    adapter.fetch = rate_limited  # type: ignore[method-assign]
    await sources.refresh_once()
    first_retry = sources._next_retry_at[adapter.stream_id]
    await sources.refresh_once()

    assert sources._failure_counts[adapter.stream_id] == 1
    assert (first_retry - datetime.now(UTC)).total_seconds() > 800
    assert adapter.health.error_code == "RATE_LIMITED"
    assert "nieuwe poging" in (adapter.health.message or "")


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


class _ConcurrentAdapter(SourceAdapter):
    owner = "parallel-test"
    expected_cadence_seconds = 60

    def __init__(self, stream_id: str, tracker: dict[str, int]) -> None:
        self.stream_id = stream_id
        self.provider = f"provider-{stream_id}"
        self.tracker = tracker
        super().__init__()

    async def fetch(self) -> AdapterResult:
        self.tracker["active"] += 1
        self.tracker["max_active"] = max(self.tracker["max_active"], self.tracker["active"])
        try:
            await asyncio.sleep(0.05)
            return AdapterResult(
                stream_id=self.stream_id,
                provider=self.provider,
                source_url=None,
                raw=self.stream_id.encode(),
                observed_at=datetime.now(UTC),
            )
        finally:
            self.tracker["active"] -= 1


async def test_source_refresh_fetches_eligible_adapters_in_parallel(services) -> None:
    _settings, _database, sources, _query, _dashboard = services
    tracker = {"active": 0, "max_active": 0}
    sources.adapters = [
        _ConcurrentAdapter("parallel_one", tracker),
        _ConcurrentAdapter("parallel_two", tracker),
    ]

    bundle = await sources.refresh_once()

    assert bundle is not None
    assert tracker["max_active"] == 2


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
                    "stationCodes": ["UT"],
                }
            ],
        )
    )
    stations_route = respx.get(NSDisruptionsAdapter.stations_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "payload": [
                    {
                        "code": "UT",
                        "UICCode": "8400621",
                        "namen": {"lang": "Utrecht Centraal"},
                        "lat": 52.0891,
                        "lng": 5.1103,
                    }
                ]
            },
        )
    )
    result = await NSDisruptionsAdapter("test-subscription").fetch()
    assert result.events[0].title == "Seinstoring Utrecht"
    assert result.events[0].location is not None
    assert result.events[0].location.label == "Utrecht Centraal"
    assert result.events[0].attributes["station_code"] == "UT"
    assert route.calls[0].request.headers["Ocp-Apim-Subscription-Key"] == "test-subscription"
    assert route.calls[0].request.url.params["isActive"] == "true"
    assert stations_route.calls[0].request.headers["Ocp-Apim-Subscription-Key"] == "test-subscription"


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
            "stationname": (("station",), np.array(["De Bilt"])),
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
    assert rows[0].metric == "wind_gust_kmh"
    assert rows[0].value == 45.0
    assert rows[0].location is not None
    assert rows[0].location.location_id == "knmi:260"
    assert rows[0].location.label == "De Bilt (260)"


def test_knmi_netcdf_parser_supports_station_first_and_wigos_filter() -> None:
    dataset = xr.Dataset(
        data_vars={
            "gff": (
                ("station", "time"),
                np.array([[10.0, 12.5], [8.0, 9.0]]),
            ),
            "latitude": (("station",), np.array([52.1, 53.0])),
            "longitude": (("station",), np.array([5.2, 6.0])),
        },
        coords={
            "time": np.array(
                [
                    np.datetime64("2026-07-10T09:50:00"),
                    np.datetime64("2026-07-10T10:00:00"),
                ]
            ),
            "station": np.array(["0-20000-0-06260", "0-20000-0-06280"]),
        },
    )
    buffer = io.BytesIO()
    dataset.to_netcdf(buffer, engine="h5netcdf")

    rows = KNMIObservationsAdapter("test", {"260"}).parse(
        buffer.getvalue(), source_url="https://api.dataplatform.knmi.nl/test"
    )

    assert len(rows) == 1
    assert rows[0].value == 45.0
    assert rows[0].location is not None
    assert rows[0].location.location_id == "knmi:0-20000-0-06260"
