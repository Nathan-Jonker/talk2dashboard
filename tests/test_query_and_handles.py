from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from talk2dashboard.domain import MeasurementRecord, SourceRef, TrustTier
from talk2dashboard.sources.base import AdapterResult
from talk2dashboard.storage.models import DataHandleRow


async def test_fixture_creates_immutable_query_handles(services):
    _settings, _database, sources, query, _dashboard = services
    bundle = await sources.initialize_fixture()
    spec = {"record_kind": "measurement", "metric": "wind_gust_kmh", "window": "PT60M"}

    first = query.execute(spec, bundle)
    second = query.execute(spec, bundle)

    assert first.handle_id == second.handle_id
    assert first.query_hash == second.query_hash
    assert first.row_count == 5
    assert first.kind == "series"
    assert first.source_bundle_version == bundle
    health = {item.stream_id: item for item in sources.health()}
    assert health["p2000"].status == "healthy"
    assert health["p2000"].provider == "recorded fixture"
    assert health["ns_disruptions"].status == "disabled"


async def test_aggregate_and_baseline_are_deterministic(services):
    _settings, _database, sources, query, _dashboard = services
    bundle = await sources.initialize_fixture()

    aggregate = query.execute(
        {"operation": "aggregate", "record_kind": "event", "group_by": "stream_id", "fn": "count"},
        bundle,
    )
    baseline = query.execute(
        {"operation": "baseline", "record_kind": "measurement", "metric": "wind_gust_kmh"},
        bundle,
    )

    assert aggregate.kind == "aggregate"
    assert aggregate.row_count == 3
    assert baseline.kind == "baseline"
    assert baseline.preview[0]["sample_size"] == 4


async def test_unknown_query_fields_are_rejected(services):
    _settings, _database, sources, query, _dashboard = services
    bundle = await sources.initialize_fixture()

    try:
        query.execute({"sql": "drop table source_bundles"}, bundle)
    except ValueError as exc:
        assert "Unsupported query fields" in str(exc)
    else:
        raise AssertionError("free SQL must never be accepted")


async def test_within_radius_filter_uses_an_immutable_origin_handle(services):
    _settings, _database, sources, query, _dashboard = services
    bundle = await sources.initialize_fixture()
    all_events = query.execute({"record_kind": "event", "limit": 100}, bundle)
    _handle, rows = query.load(all_events.handle_id)
    origin_row = next(row for row in rows if row["record_id"] == "fixture-p2000-001")
    origin = query.create_handle("events", {"fixture_origin": True}, [origin_row], bundle)

    nearby = query.execute(
        {
            "record_kind": "event",
            "filters": [
                {
                    "op": "within_radius_handle",
                    "field": "location",
                    "handle_id": origin.handle_id,
                    "radius_m": 5000,
                }
            ],
            "limit": 100,
        },
        bundle,
    )
    assert nearby.row_count >= 1
    assert all(row.get("location") for row in nearby.preview)


async def test_live_baseline_requires_fourteen_days_of_history(services):
    _settings, _database, sources, query, _dashboard = services
    now = datetime.now(UTC)
    measurements = []
    for index, value in enumerate((10.0, 12.0, 14.0)):
        observed = now - timedelta(hours=2 - index)
        source_ref = SourceRef(
            stream_id="live_test",
            record_id=f"live-{index}",
            source_url="https://example.invalid/measurements",
            owner="Contract test",
            trust_tier=TrustTier.OFFICIAL_MEASUREMENT,
            observed_at=observed,
            ingested_at=now,
        )
        measurements.append(
            MeasurementRecord(
                record_id=f"live-{index}",
                stream_id="live_test",
                metric="water_level_cm",
                value=value,
                unit="cm",
                observed_at=observed,
                ingested_at=now,
                source_ref=source_ref,
            )
        )
    bundle = sources.persist_results(
        [
            AdapterResult(
                stream_id="live_test",
                provider="contract-test",
                source_url="https://example.invalid/measurements",
                raw=b"live-baseline-contract",
                observed_at=now,
                measurements=measurements,
            )
        ]
    )
    with pytest.raises(ValueError, match="fourteen required"):
        query.execute(
            {
                "operation": "baseline",
                "record_kind": "measurement",
                "stream": "live_test",
                "metric": "water_level_cm",
            },
            bundle,
        )


async def test_expired_handle_cannot_be_reused(services):
    _settings, database, sources, query, _dashboard = services
    bundle = await sources.initialize_fixture()
    handle = query.create_handle("events", {"ephemeral": True}, [], bundle, ttl_seconds=60)
    with database.session() as session:
        row = session.get(DataHandleRow, handle.handle_id)
        assert row is not None
        row.expires_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with pytest.raises(KeyError, match="Expired handle"):
        query.load(handle.handle_id)
