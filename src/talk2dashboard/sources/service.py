from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from talk2dashboard.config import Settings
from talk2dashboard.domain import EventRecord, SourceHealth, TrustTier
from talk2dashboard.incidents import cluster_events
from talk2dashboard.sources.base import (
    AdapterResult,
    DisabledAdapter,
    FallbackAdapter,
    SourceAdapter,
)
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
from talk2dashboard.storage.database import Database
from talk2dashboard.storage.models import (
    IncidentClusterRow,
    NormalizedRecordRow,
    SourceBundleRow,
    SourceSnapshotRow,
)

logger = logging.getLogger(__name__)


class SourceService:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self.adapters = self._build_adapters()
        self._subscribers: set[asyncio.Queue[dict]] = set()
        self._stop = asyncio.Event()

    def _build_adapters(self) -> list[SourceAdapter]:
        adapters: list[SourceAdapter] = [
            RSSAdapter(
                stream_id="nos_rss",
                owner="NOS",
                provider="NOS RSS",
                url="https://feeds.nos.nl/nosnieuwsalgemeen",
                category="media_context",
                trust_tier=TrustTier.MEDIA_CONTEXT,
                cadence_seconds=300,
            )
        ]
        p2000_rss = RSSAdapter(
            stream_id="p2000",
            owner="Alarmeringen.nl",
            provider="Alarmeringen RSS fallback",
            url="https://alarmeringen.nl/feeds/all.rss",
            category="p2000_signal",
            trust_tier=TrustTier.SIGNAL,
            cadence_seconds=60,
        )
        if self.settings.p2000_provider_mode == "disabled":
            adapters.append(DisabledAdapter("p2000", "P2000", "P2000 source disabled"))
        elif self.settings.p2000_provider_mode == "rss":
            adapters.append(p2000_rss)
        elif self.settings.p2000_provider_api_key:
            radar = Radar112Adapter(self.settings.p2000_provider_api_key)
            adapters.append(
                FallbackAdapter(radar, p2000_rss)
                if self.settings.p2000_provider_mode == "auto"
                else radar
            )
        elif self.settings.p2000_provider_mode == "112radar":
            adapters.append(DisabledAdapter("p2000", "112Radar", "112Radar API key ontbreekt"))
        else:
            adapters.append(p2000_rss)
        adapters.extend([RWSWaterAdapter(), NDWIncidentAdapter(), LuchtmeetnetAdapter()])
        if self.settings.knmi_api_key:
            station_ids = {
                value.strip()
                for value in self.settings.knmi_station_ids.split(",")
                if value.strip()
            }
            adapters.append(KNMIObservationsAdapter(self.settings.knmi_api_key, station_ids))
        else:
            adapters.append(
                DisabledAdapter("knmi_observations", "KNMI", "KNMI Open Data API key ontbreekt")
            )
        if self.settings.ns_api_subscription_key:
            adapters.append(NSDisruptionsAdapter(self.settings.ns_api_subscription_key))
        else:
            adapters.append(
                DisabledAdapter("ns_disruptions", "NS", "NS API subscription key ontbreekt")
            )
        return adapters

    async def initialize_fixture(self) -> str:
        adapter = FixtureAdapter(self.settings.fixture_dir / "operational_snapshot.json")
        result = await adapter.fetch()
        for configured in self.adapters:
            events = [
                record for record in result.events if record.stream_id == configured.stream_id
            ]
            measurements = [
                record for record in result.measurements if record.stream_id == configured.stream_id
            ]
            if not events and not measurements:
                continue
            configured.mark_success(
                AdapterResult(
                    stream_id=configured.stream_id,
                    provider="recorded fixture",
                    source_url=None,
                    raw=result.raw,
                    observed_at=max(
                        (record.observed_at for record in [*events, *measurements]),
                        default=result.observed_at,
                    ),
                    events=events,
                    measurements=measurements,
                    metadata=result.metadata,
                )
            )
        return self.persist_results([result])

    async def refresh_once(self) -> str | None:
        results: list[AdapterResult] = []
        for adapter in self.adapters:
            if isinstance(adapter, DisabledAdapter):
                continue
            try:
                result = await adapter.fetch()
                adapter.mark_success(result)
                results.append(result)
            except Exception as exc:
                adapter.mark_failure(type(exc).__name__, str(exc))
                logger.warning("Source %s failed: %s", adapter.stream_id, type(exc).__name__)
        if not results:
            return None
        bundle = self.persist_results(results)
        await self.publish({"type": "source_bundle", "source_bundle_version": bundle})
        return bundle

    def persist_results(self, results: list[AdapterResult]) -> str:
        snapshot_ids: list[str] = []
        updated_streams: set[str] = set()
        with self.database.session() as session:
            for result in results:
                digest = hashlib.sha256(result.raw).hexdigest()
                raw_path = self.settings.raw_dir / digest[:2] / digest
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                if not raw_path.exists():
                    raw_path.write_bytes(result.raw)
                records = [*result.events, *result.measurements]
                records_by_stream: dict[str, list] = {}
                for record in records:
                    records_by_stream.setdefault(record.stream_id, []).append(record)
                if not records_by_stream:
                    records_by_stream[result.stream_id] = []
                for stream_id, stream_records in records_by_stream.items():
                    updated_streams.add(stream_id)
                    snapshot_id = f"snap_{uuid4().hex}"
                    snapshot_ids.append(snapshot_id)
                    session.add(
                        SourceSnapshotRow(
                            snapshot_id=snapshot_id,
                            stream_id=stream_id,
                            provider=result.provider,
                            content_hash=digest,
                            source_url=result.source_url,
                            observed_at=max(
                                (record.observed_at for record in stream_records),
                                default=result.observed_at,
                            ).isoformat(),
                            ingested_at=datetime.now(UTC).isoformat(),
                            metadata_json=json.dumps(result.metadata, ensure_ascii=True),
                        )
                    )
                    session.flush()
                    for record in stream_records:
                        location = record.location
                        record_version_id = hashlib.sha256(
                            f"{snapshot_id}:{record.record_id}".encode()
                        ).hexdigest()
                        session.add(
                            NormalizedRecordRow(
                                record_version_id=record_version_id,
                                record_id=record.record_id,
                                snapshot_id=snapshot_id,
                                stream_id=record.stream_id,
                                record_kind="event"
                                if hasattr(record, "category")
                                else "measurement",
                                observed_at=record.observed_at.isoformat(),
                                latitude=location.latitude if location else None,
                                longitude=location.longitude if location else None,
                                payload_json=record.model_dump_json(),
                            )
                        )
            existing_streams = session.scalars(select(SourceSnapshotRow.stream_id).distinct()).all()
            for stream_id in existing_streams:
                if stream_id in updated_streams:
                    continue
                previous = session.scalars(
                    select(SourceSnapshotRow)
                    .where(SourceSnapshotRow.stream_id == stream_id)
                    .order_by(SourceSnapshotRow.ingested_at.desc())
                ).first()
                if previous:
                    snapshot_ids.append(previous.snapshot_id)
            bundle_version = f"bundle_{hashlib.sha256(json.dumps(sorted(snapshot_ids)).encode()).hexdigest()[:20]}"
            session.add(
                SourceBundleRow(
                    bundle_version=bundle_version,
                    snapshot_ids_json=json.dumps(snapshot_ids),
                    health_json=json.dumps(
                        [item.model_dump(mode="json") for item in self.health()], ensure_ascii=True
                    ),
                )
            )
            session.flush()
            event_payloads = session.scalars(
                select(NormalizedRecordRow.payload_json).where(
                    NormalizedRecordRow.snapshot_id.in_(snapshot_ids),
                    NormalizedRecordRow.record_kind == "event",
                )
            ).all()
            events = [EventRecord.model_validate_json(value) for value in event_payloads]
            for cluster in cluster_events(
                events,
                distance_m=self.settings.cluster_join_distance_m,
                window_minutes=self.settings.cluster_join_window_minutes,
                active_min_events=self.settings.cluster_active_min_events,
            ):
                session.add(
                    IncidentClusterRow(
                        incident_version_id=hashlib.sha256(
                            f"{bundle_version}:{cluster.incident_id}".encode()
                        ).hexdigest(),
                        incident_id=cluster.incident_id,
                        bundle_version=bundle_version,
                        first_seen_at=cluster.first_seen_at.isoformat(),
                        last_seen_at=cluster.last_seen_at.isoformat(),
                        status=cluster.status,
                        confidence=cluster.confidence,
                        payload_json=cluster.model_dump_json(),
                    )
                )
        return bundle_version

    def bundle_has_records(self, bundle_version: str) -> bool:
        with self.database.session() as session:
            bundle = session.get(SourceBundleRow, bundle_version)
            if bundle is None:
                return False
            snapshot_ids = json.loads(bundle.snapshot_ids_json)
            return (
                session.scalars(
                    select(NormalizedRecordRow.record_version_id)
                    .where(NormalizedRecordRow.snapshot_id.in_(snapshot_ids))
                    .limit(1)
                ).first()
                is not None
            )

    def latest_bundle_version(self) -> str | None:
        with self.database.session() as session:
            row = session.scalars(
                select(SourceBundleRow).order_by(SourceBundleRow.created_at.desc())
            ).first()
            return row.bundle_version if row else None

    def health(self) -> list[SourceHealth]:
        return [adapter.health for adapter in self.adapters]

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.refresh_once()
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.source_refresh_seconds
                )
            except TimeoutError:
                continue

    def stop(self) -> None:
        self._stop.set()

    async def subscribe(self) -> AsyncIterator[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=32)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    async def publish(self, event: dict) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(event)
