from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from talk2dashboard.claims import audit_numeric_claims
from talk2dashboard.domain import EventRecord, LocationRef, SourceRef, ToolRequest, TrustTier
from talk2dashboard.errors import InsufficientSeriesError
from talk2dashboard.incidents import cluster_events
from talk2dashboard.integrations.places import GeocodingClient, PlacesClient
from talk2dashboard.integrations.search import BraveSearchClient
from talk2dashboard.locations import EphemeralLocationStore
from talk2dashboard.storage.models import ClaimAuditRow, DataHandleRow, ToolAuditRow
from talk2dashboard.tools.executor import ToolExecutor


def _event(
    record_id: str,
    observed_at: datetime,
    *,
    latitude: float,
    longitude: float,
    radius: float = 0,
    severity: str = "medium",
    stream_id: str = "p2000",
) -> EventRecord:
    ref = SourceRef(
        stream_id=stream_id,
        record_id=record_id,
        owner="test",
        trust_tier=TrustTier.FIXTURE,
        observed_at=observed_at,
        ingested_at=observed_at,
    )
    return EventRecord(
        record_id=record_id,
        stream_id=stream_id,
        category="incident",
        title=record_id,
        severity=severity,  # type: ignore[arg-type]
        observed_at=observed_at,
        ingested_at=observed_at,
        location=LocationRef(
            location_id=f"loc:{record_id}",
            label=record_id,
            latitude=latitude,
            longitude=longitude,
            uncertainty_radius_m=radius,
            geometry_source="fixture",
            source_refs=(ref,),
        ),
        source_ref=ref,
    )


def test_clustering_uses_uncertainty_severity_and_explicit_clock() -> None:
    observed = datetime(2026, 7, 10, 10, tzinfo=UTC)
    uncertain = [
        _event("a", observed, latitude=52.0, longitude=5.0, radius=600),
        _event("b", observed + timedelta(minutes=10), latitude=52.0, longitude=5.04, radius=600),
    ]
    clusters = cluster_events(
        uncertain,
        distance_m=2000,
        window_minutes=30,
        active_min_events=2,
        as_of=observed + timedelta(minutes=20),
    )
    assert len(clusters) == 1
    assert clusters[0].status == "active"
    high = cluster_events(
        [_event("high", observed, latitude=52.0, longitude=5.0, severity="high")],
        distance_m=2000,
        window_minutes=30,
        active_min_events=2,
        as_of=observed + timedelta(minutes=121),
    )
    assert high[0].status == "resolved"


async def _executor(services) -> ToolExecutor:
    settings, database, sources, query, dashboard = services
    bundle = await sources.initialize_fixture()
    dashboard.ensure_default(bundle)
    return ToolExecutor(
        database,
        sources,
        query,
        dashboard,
        PlacesClient(settings, database),
        GeocodingClient(settings, database),
        BraveSearchClient(settings, database),
    )


async def test_typed_version_conflict_is_idempotently_preserved(services) -> None:
    executor = await _executor(services)
    policy = executor.policy()
    request = ToolRequest(
        request_id="typed-conflict",
        session_policy_version=policy["version"],
        dashboard_version=99,
        payload={"expected_version": 99, "operations": []},
    )
    first = await executor.execute("dashboard_batch", request)
    second = await executor.execute("dashboard_batch", request)
    assert first.error == second.error
    assert first.error and first.error["code"] == "VERSION_CONFLICT"
    assert first.error["details"]["current_version"] == 1
    assert first.error["retryable"] is True


async def test_inspect_filters_ids_and_advertises_radius(services) -> None:
    executor = await _executor(services)
    response = await executor.execute(
        "inspect_workspace",
        ToolRequest(
            request_id="inspect-filtered",
            session_policy_version=executor.policy()["version"],
            payload={
                "sections": ["streams", "stream_schema", "dashboard"],
                "ids": ["p2000", "incident-map"],
                "detail": "compact",
            },
        ),
    )
    assert response.ok and isinstance(response.result, dict)
    assert [item["stream_id"] for item in response.result["streams"]] == ["p2000"]
    assert "within_radius_handle" in response.result["stream_contract"]["filter_ops"]
    assert [item["panel_id"] for item in response.result["dashboard"]["panels"]] == ["incident-map"]


class _Geocoding:
    async def resolve(self, _text: str) -> dict:
        return {
            "matches": [
                {
                    "place_id": "google-place",
                    "display_label": "Utrecht, Nederland",
                    "location": {"lat": 52.09, "lng": 5.12},
                }
            ]
        }


class _Places:
    async def nearby(self, **_kwargs) -> dict:
        return {
            "places": [
                {
                    "id": "far",
                    "displayName": {"text": "Ziekenhuis verder weg"},
                    "primaryType": "hospital",
                    "location": {"latitude": 52.48, "longitude": 4.63},
                    "googleMapsUri": "https://maps.example/far",
                },
                {
                    "id": "near",
                    "displayName": {"text": "Ziekenhuis dichtbij"},
                    "primaryType": "hospital",
                    "location": {"latitude": 52.458, "longitude": 4.607},
                    "googleMapsUri": "https://maps.example/near",
                },
            ],
            "attribution": "Google Maps",
            "budget": {"used_today": 1, "limit": 100, "warning": False},
            "request": {
                "radius_m": 5000,
                "included_types": ["hospital"],
                "field_mask": "minimal",
            },
        }


class _RecordingPlaces(_Places):
    def __init__(self) -> None:
        self.request: dict = {}

    async def nearby(self, **kwargs) -> dict:
        self.request = kwargs
        return await super().nearby(**kwargs)


async def test_google_resolution_is_ephemeral_not_a_data_handle(services) -> None:
    executor = await _executor(services)
    executor.geocoding = _Geocoding()  # type: ignore[assignment]
    response = await executor.execute(
        "data_batch",
        ToolRequest(
            request_id="ephemeral-location",
            session_policy_version=executor.policy()["version"],
            payload={
                "operations": [
                    {
                        "operation": "resolve_location",
                        "text": "Utrecht Centraal",
                        "resolver": "google",
                        "save_as": "origin",
                    }
                ]
            },
        ),
    )
    assert response.ok and isinstance(response.result, dict)
    resolution_id = response.result["aliases"]["origin"]
    assert resolution_id.startswith("locres_")
    _settings, database, *_ = services
    assert EphemeralLocationStore(database).get(resolution_id).display_label == "Utrecht, Nederland"
    with database.session() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(DataHandleRow)
                .where(DataHandleRow.kind == "location")
            )
            == 0
        )


async def test_nearby_places_resolves_named_origin_and_returns_distance(services) -> None:
    executor = await _executor(services)
    executor.geocoding = _Geocoding()  # type: ignore[assignment]
    executor.places = _Places()  # type: ignore[assignment]
    response = await executor.execute(
        "nearby_places",
        ToolRequest(
            request_id="nearby-ijmuiden",
            session_policy_version=executor.policy()["version"],
            payload={
                "origin_text": "IJmuiden",
                "included_types": ["hospital"],
                "radius_m": 5000,
                "rank": "distance",
            },
        ),
    )
    assert response.ok and isinstance(response.result, dict)
    assert response.result["origin"]["label"] == "Utrecht, Nederland"
    assert response.result["nearest"]["record_id"] == "near"
    assert response.result["nearest"]["distance_m"] >= 0
    assert response.result["preview"][0]["record_id"] == "near"
    handle_id = response.result["places_handle"]["handle_id"]
    _handle, map_rows = executor.query.load(handle_id)
    assert map_rows[0]["is_origin"] is True
    assert map_rows[0]["distance_m"] == 0
    assert map_rows[0]["title"] == "Utrecht, Nederland"


async def test_nearby_places_uses_supported_subset_and_reports_ignored_types(services) -> None:
    executor = await _executor(services)
    executor.geocoding = _Geocoding()  # type: ignore[assignment]
    places = _RecordingPlaces()
    executor.places = places  # type: ignore[assignment]
    response = await executor.execute(
        "nearby_places",
        ToolRequest(
            request_id="nearby-mixed-types",
            session_policy_version=executor.policy()["version"],
            payload={
                "origin_text": "Almere centrum",
                "included_types": ["restaurant", "hospital", "cafe", "pharmacy"],
                "radius_m": 5000,
                "rank": "distance",
            },
        ),
    )
    assert response.ok and isinstance(response.result, dict)
    assert places.request["included_types"] == ["hospital", "pharmacy"]
    assert response.result["warnings"] == [
        {
            "code": "PLACE_TYPES_IGNORED",
            "message": "Niet-ondersteunde voorzieningstypen zijn overgeslagen.",
            "ignored_types": ["restaurant", "cafe"],
            "used_types": ["hospital", "pharmacy"],
        }
    ]


async def test_nearby_places_accepts_resolution_in_legacy_origin_handle(services) -> None:
    executor = await _executor(services)
    executor.geocoding = _Geocoding()  # type: ignore[assignment]
    executor.places = _Places()  # type: ignore[assignment]
    resolved = await executor.execute(
        "data_batch",
        ToolRequest(
            request_id="resolve-nearby-origin",
            session_policy_version=executor.policy()["version"],
            payload={
                "operations": [
                    {
                        "operation": "resolve_location",
                        "text": "IJmuiden",
                        "resolver": "google",
                        "save_as": "origin",
                    }
                ]
            },
        ),
    )
    assert resolved.ok and isinstance(resolved.result, dict)
    response = await executor.execute(
        "nearby_places",
        ToolRequest(
            request_id="nearby-legacy-origin-handle",
            session_policy_version=executor.policy()["version"],
            payload={
                "origin_handle": resolved.result["aliases"]["origin"],
                "included_types": ["hospital"],
                "radius_m": 5000,
            },
        ),
    )
    assert response.ok and isinstance(response.result, dict)
    assert response.result["origin"]["reference"].startswith("locres_")


def test_claim_audit_matches_numbers_to_same_conversation_tools(services) -> None:
    _settings, database, *_ = services
    now = datetime.now(UTC).isoformat()
    with database.session() as session:
        session.add(
            ToolAuditRow(
                audit_id="audit-claim",
                conversation_id="conversation-1",
                turn_id="turn-1",
                request_id="request-claim",
                tool_name="data_batch",
                arguments_json="{}",
                result_json='{"value": 42}',
                ok=True,
                error_code=None,
                error_json=None,
                started_ns=1,
                ended_ns=2,
                duration_ms=0.001,
                dashboard_before=1,
                dashboard_after=1,
                source_bundle_version=None,
                created_at=now,
            )
        )
    results = audit_numeric_claims(
        database,
        event_id="event-claim",
        conversation_id="conversation-1",
        turn_id="turn-1",
        text="Er zijn 42 meldingen en 99 onbevestigde signalen.",
    )
    assert [item["status"] for item in results] == ["supported", "unsupported"]
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(ClaimAuditRow)) == 2


def test_insufficient_series_has_machine_readable_details() -> None:
    error = InsufficientSeriesError(2)
    assert error.as_dict()["details"] == {"available_pairs": 2, "required_pairs": 3}


async def test_fixture_health_and_stale_control_are_explicit(services) -> None:
    _settings, _database, sources, *_ = services
    await sources.initialize_fixture()
    fixture_health = {item.stream_id: item for item in sources.health()}
    assert fixture_health["p2000"].fixture is True
    assert fixture_health["p2000"].status == "fixture"
    sources.control_fixture("p2000", "stale")
    stale = {item.stream_id: item for item in sources.health()}["p2000"]
    assert stale.status == "stale"
    assert stale.age_seconds and stale.age_seconds > stale.expected_cadence_seconds


async def test_places_rejects_unbounded_types_before_provider_call(services) -> None:
    settings, database, *_ = services
    client = PlacesClient(settings, database)
    with pytest.raises(ValueError, match="unsupported Place types"):
        await client.nearby(
            latitude=52.0,
            longitude=5.0,
            included_types=["night_club"],
            radius_m=5000,
            max_results=5,
            rank="distance",
            fields_profile="minimal",
        )


async def test_places_uses_and_revalidates_primary_types(services, respx_mock) -> None:
    settings, database, *_ = services
    client = PlacesClient(
        settings.model_copy(update={"google_places_server_api_key": "test-key"}), database
    )
    route = respx_mock.post(PlacesClient.ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "places": [
                    {"id": "adjacent", "primaryType": "medical_center"},
                    {"id": "hospital", "primaryType": "hospital"},
                ]
            },
        )
    )
    result = await client.nearby(
        latitude=52.45,
        longitude=4.60,
        included_types=["hospital"],
        radius_m=5000,
        max_results=5,
        rank="distance",
        fields_profile="minimal",
    )
    request_body = json.loads(route.calls.last.request.content)
    assert request_body["includedPrimaryTypes"] == ["hospital"]
    assert "includedTypes" not in request_body
    assert [item["id"] for item in result["places"]] == ["hospital"]


async def test_search_uses_keyless_duckduckgo_backup(services, monkeypatch) -> None:
    settings, database, *_ = services

    class FakeDDGS:
        def text(self, query, **options):
            assert query == "laatste nieuws Almere"
            assert options == {
                "region": "nl-nl",
                "safesearch": "moderate",
                "max_results": 5,
                "timelimit": "w",
            }
            return [
                {
                    "title": "Nieuws uit Almere",
                    "href": "https://example.test/almere",
                    "body": "Actueel lokaal nieuws.",
                }
            ]

    monkeypatch.setattr("talk2dashboard.integrations.search.DDGS", FakeDDGS)
    results = await BraveSearchClient(
        settings.model_copy(update={"brave_search_api_key": ""}), database
    ).search("laatste nieuws Almere", max_results=5, recency_days=7)

    assert results[0]["title"] == "Nieuws uit Almere"
    assert results[0]["provider"] == "DuckDuckGo"


async def test_search_uses_google_news_if_duckduckgo_fails(
    services, respx_mock, monkeypatch
) -> None:
    settings, database, *_ = services

    class FailingDDGS:
        def text(self, _query, **_options):
            raise RuntimeError("temporary DDGS failure")

    monkeypatch.setattr("talk2dashboard.integrations.search.DDGS", FailingDDGS)
    route = respx_mock.get(BraveSearchClient.GOOGLE_NEWS_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            content=b"""<?xml version='1.0'?><rss><channel><item>
            <title>Nieuws uit Almere</title><link>https://example.test/almere</link>
            <pubDate>Tue, 14 Jul 2026 10:00:00 GMT</pubDate>
            <source>Omroep Flevoland</source></item></channel></rss>""",
        )
    )

    results = await BraveSearchClient(
        settings.model_copy(update={"brave_search_api_key": ""}), database
    ).search("laatste nieuws Almere", max_results=5, recency_days=7)

    assert route.called
    assert results[0]["title"] == "Nieuws uit Almere"
    assert results[0]["provider"] == "Google News RSS"
