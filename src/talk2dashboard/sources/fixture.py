from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from talk2dashboard.domain import EventRecord, LocationRef, MeasurementRecord, SourceRef, TrustTier
from talk2dashboard.sources.base import AdapterResult, SourceAdapter


class FixtureAdapter(SourceAdapter):
    stream_id = "fixture_demo"
    owner = "Talk2Dashboard"
    expected_cadence_seconds = 3600
    provider = "fixture"

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    async def fetch(self) -> AdapterResult:
        raw = self.path.read_bytes()
        payload = json.loads(raw)
        ingested_at = datetime.now(UTC)
        events: list[EventRecord] = []
        measurements: list[MeasurementRecord] = []
        for item in payload.get("events", []):
            observed_at = datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00"))
            location_data = item.get("location")
            source_ref = SourceRef(
                stream_id=item["stream_id"],
                record_id=item["record_id"],
                owner=item.get("owner", self.owner),
                trust_tier=TrustTier(item.get("trust_tier", "fixture")),
                observed_at=observed_at,
                ingested_at=ingested_at,
            )
            location = None
            if location_data:
                location = LocationRef(
                    location_id=f"fixture:{item['record_id']}",
                    label=location_data["label"],
                    latitude=location_data["latitude"],
                    longitude=location_data["longitude"],
                    uncertainty_radius_m=location_data.get("uncertainty_radius_m", 0),
                    geometry_source="fixture",
                    source_refs=(source_ref,),
                )
            events.append(
                EventRecord(
                    record_id=item["record_id"],
                    stream_id=item["stream_id"],
                    category=item["category"],
                    title=item["title"],
                    description=item.get("description"),
                    severity=item.get("severity", "unknown"),
                    status=item.get("status", "active"),
                    observed_at=observed_at,
                    ingested_at=ingested_at,
                    location=location,
                    attributes=item.get("attributes", {}),
                    source_ref=source_ref,
                    quality_flags=("synthetic_fixture",),
                )
            )
        for item in payload.get("measurements", []):
            observed_at = datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00"))
            source_ref = SourceRef(
                stream_id=item["stream_id"],
                record_id=item["record_id"],
                owner=item.get("owner", self.owner),
                trust_tier=TrustTier.FIXTURE,
                observed_at=observed_at,
                ingested_at=ingested_at,
            )
            location = LocationRef(
                location_id=f"fixture:{item['record_id']}:station",
                label=item.get("station", "Fixture station"),
                latitude=item["latitude"],
                longitude=item["longitude"],
                geometry_source="fixture",
                source_refs=(source_ref,),
            )
            measurements.append(
                MeasurementRecord(
                    record_id=item["record_id"],
                    stream_id=item["stream_id"],
                    metric=item["metric"],
                    value=item["value"],
                    unit=item["unit"],
                    observed_at=observed_at,
                    ingested_at=ingested_at,
                    location=location,
                    source_ref=source_ref,
                    quality_flags=("synthetic_fixture",),
                )
            )
        return AdapterResult(
            stream_id=self.stream_id,
            provider=self.provider,
            source_url=None,
            raw=raw,
            observed_at=datetime.fromisoformat(payload["recorded_at"].replace("Z", "+00:00")),
            events=events,
            measurements=measurements,
            metadata={"fixture_id": payload["fixture_id"], "synthetic": True},
        )
