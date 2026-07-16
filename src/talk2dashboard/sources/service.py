from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from sqlalchemy import func, select

from talk2dashboard.config import Settings
from talk2dashboard.domain import (
    EventRecord,
    MeasurementRecord,
    SourceHealth,
    SourceHealthStatus,
    TrustTier,
)
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
    SessionPolicyRow,
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
        self._fixture_controls: dict[str, str] = {}
        self._fixture_name = "operational_snapshot.json"
        self._failure_counts: dict[str, int] = {}
        self._next_retry_at: dict[str, datetime] = {}
        self._restore_health_from_store()
        self._repair_knmi_snapshot()
        self._repair_ndw_snapshot()

    @staticmethod
    def _timestamp(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)

    def _restore_health_from_store(self) -> None:
        with self.database.session() as session:
            for adapter in self.adapters:
                snapshot = session.scalars(
                    select(SourceSnapshotRow)
                    .where(SourceSnapshotRow.stream_id == adapter.stream_id)
                    .order_by(SourceSnapshotRow.ingested_at.desc())
                ).first()
                if snapshot is None:
                    continue
                record_count = (
                    session.scalar(
                        select(func.count())
                        .select_from(NormalizedRecordRow)
                        .where(NormalizedRecordRow.snapshot_id == snapshot.snapshot_id)
                    )
                    or 0
                )
                metadata = json.loads(snapshot.metadata_json or "{}")
                is_fixture = snapshot.provider in {"fixture", "recorded fixture"} or bool(
                    metadata.get("fixture_id")
                )
                requires_location_enrichment = (
                    adapter.stream_id == "luchtmeetnet"
                    and int(metadata.get("stations_with_coordinates") or 0) == 0
                    and not is_fixture
                )
                adapter.health = adapter.health.model_copy(
                    update={
                        "status": (
                            SourceHealthStatus.FIXTURE
                            if is_fixture
                            else (
                                SourceHealthStatus.HEALTHY
                                if record_count > 0
                                else SourceHealthStatus.DEGRADED
                            )
                        ),
                        # Older Luchtmeetnet snapshots were created from the
                        # paginated station list only and therefore lacked labels
                        # and coordinates. Force one refresh after upgrading.
                        "last_success_at": (
                            None
                            if requires_location_enrichment
                            else self._timestamp(snapshot.ingested_at)
                        ),
                        "newest_record_at": self._timestamp(snapshot.observed_at),
                        "record_count": record_count,
                        "message": (
                            "Stationmetadata wordt verrijkt"
                            if requires_location_enrichment
                            else None
                            if record_count > 0
                            else "Geen bruikbare records ontvangen"
                        ),
                        "provider": snapshot.provider,
                        "fixture": is_fixture,
                        "fallback": bool(metadata.get("fallback_from")),
                    }
                )

    def _repair_knmi_snapshot(self) -> None:
        adapter = next(
            (item for item in self.adapters if isinstance(item, KNMIObservationsAdapter)),
            None,
        )
        if adapter is None:
            return
        with self.database.session() as session:
            snapshot = session.scalars(
                select(SourceSnapshotRow)
                .where(SourceSnapshotRow.stream_id == adapter.stream_id)
                .order_by(SourceSnapshotRow.ingested_at.desc())
            ).first()
            has_dashboard_metric = bool(
                snapshot
                and session.scalars(
                    select(NormalizedRecordRow.record_version_id)
                    .where(NormalizedRecordRow.snapshot_id == snapshot.snapshot_id)
                    .where(NormalizedRecordRow.payload_json.contains('"wind_gust_kmh"'))
                    .limit(1)
                ).first()
            )
            metadata = json.loads(snapshot.metadata_json or "{}") if snapshot else {}
        if snapshot is None or (
            adapter.health.record_count > 0
            and has_dashboard_metric
            and metadata.get("station_labels") == "official"
        ):
            return
        raw_path = self.settings.raw_dir / snapshot.content_hash[:2] / snapshot.content_hash
        if not raw_path.is_file():
            return
        try:
            raw = raw_path.read_bytes()
            measurements = adapter.parse(raw, source_url=snapshot.source_url or "KNMI Open Data")
        except Exception as exc:
            logger.warning("Stored KNMI snapshot could not be reparsed: %s", type(exc).__name__)
            return
        if not measurements:
            return
        result = AdapterResult(
            stream_id=adapter.stream_id,
            provider=snapshot.provider,
            source_url=snapshot.source_url,
            raw=raw,
            observed_at=max(item.observed_at for item in measurements),
            measurements=measurements,
            metadata={**metadata, "station_labels": "official"},
        )
        adapter.mark_success(result)
        self.persist_results([result])

    def _repair_ndw_snapshot(self) -> None:
        adapter = next(
            (item for item in self.adapters if isinstance(item, NDWIncidentAdapter)), None
        )
        if adapter is None:
            return
        with self.database.session() as session:
            snapshot = session.scalars(
                select(SourceSnapshotRow)
                .where(SourceSnapshotRow.stream_id == adapter.stream_id)
                .order_by(SourceSnapshotRow.ingested_at.desc())
            ).first()
            metadata = json.loads(snapshot.metadata_json or "{}") if snapshot else {}
        if snapshot is None or metadata.get("descriptions") == "humanized-v1":
            return
        raw_path = self.settings.raw_dir / snapshot.content_hash[:2] / snapshot.content_hash
        if not raw_path.is_file():
            return
        try:
            raw = raw_path.read_bytes()
            events = adapter.parse(raw)
        except Exception as exc:
            logger.warning("Stored NDW snapshot could not be reparsed: %s", type(exc).__name__)
            return
        if not events:
            return
        result = AdapterResult(
            stream_id=adapter.stream_id,
            provider=snapshot.provider,
            source_url=snapshot.source_url,
            raw=raw,
            observed_at=max(item.observed_at for item in events),
            events=events,
            metadata={**metadata, "descriptions": "humanized-v1"},
        )
        adapter.mark_success(result)
        self.persist_results([result])

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
        adapter = FixtureAdapter(self.settings.fixture_dir / self._fixture_name)
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
        eligible: list[tuple[SourceAdapter, datetime]] = []
        for adapter in self.adapters:
            if isinstance(adapter, DisabledAdapter):
                continue
            now = datetime.now(UTC)
            if now < self._next_retry_at.get(adapter.stream_id, now):
                continue
            if (
                adapter.health.last_success_at
                and (now - adapter.health.last_success_at).total_seconds()
                < adapter.expected_cadence_seconds
            ):
                continue
            eligible.append((adapter, now))

        fetched = await asyncio.gather(
            *(adapter.fetch() for adapter, _now in eligible),
            return_exceptions=True,
        )
        for (adapter, now), outcome in zip(eligible, fetched, strict=True):
            if not isinstance(outcome, BaseException):
                result = outcome
                adapter.mark_success(result)
                self._failure_counts.pop(adapter.stream_id, None)
                self._next_retry_at.pop(adapter.stream_id, None)
                results.append(result)
                continue

            exc = outcome
            if isinstance(exc, asyncio.CancelledError):
                raise exc
            if isinstance(exc, Exception):
                failures = self._failure_counts.get(adapter.stream_id, 0) + 1
                self._failure_counts[adapter.stream_id] = failures
                base_delay = max(adapter.expected_cadence_seconds, 300)
                retry_delay = min(base_delay * (2 ** (failures - 1)), 3600)
                code = type(exc).__name__
                message = str(exc)
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                    code = "RATE_LIMITED"
                    retry_delay = max(retry_delay, 900)
                    retry_after = exc.response.headers.get("Retry-After", "")
                    if retry_after.isdigit():
                        retry_delay = max(retry_delay, min(int(retry_after), 3600))
                    minutes = max(1, round(retry_delay / 60))
                    message = f"Bronlimiet bereikt; nieuwe poging over {minutes} minuten."
                self._next_retry_at[adapter.stream_id] = now + timedelta(seconds=retry_delay)
                adapter.mark_failure(code, message)
                logger.warning("Source %s failed: %s", adapter.stream_id, type(exc).__name__)
        if not results:
            return None
        # Parsing and persistence can involve thousands of records. Keep this work
        # away from the ASGI event loop so voice, tool and render endpoints remain
        # responsive while sources refresh.
        bundle = await asyncio.to_thread(self.persist_results, results)
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
                records_by_stream: dict[str, dict[str, EventRecord | MeasurementRecord]] = {}
                for record in records:
                    records_by_stream.setdefault(record.stream_id, {})[record.record_id] = record
                if not records_by_stream:
                    records_by_stream[result.stream_id] = {}
                for stream_id, records_by_id in records_by_stream.items():
                    stream_records = list(records_by_id.values())
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

        # Incident clustering is CPU-heavy for large event bundles. Running it
        # inside the snapshot write transaction used to block interactive handle
        # creation for the full clustering duration. Read the committed bundle,
        # cluster without a transaction, then persist only the compact result.
        with self.database.session() as session:
            event_payloads = session.scalars(
                select(NormalizedRecordRow.payload_json).where(
                    NormalizedRecordRow.snapshot_id.in_(snapshot_ids),
                    NormalizedRecordRow.record_kind == "event",
                )
            ).all()
        events = [EventRecord.model_validate_json(value) for value in event_payloads]
        clusters = cluster_events(
            events,
            distance_m=self.settings.cluster_join_distance_m,
            window_minutes=self.settings.cluster_join_window_minutes,
            active_min_events=self.settings.cluster_active_min_events,
            resolve_after_minutes=self.settings.cluster_resolve_after_minutes,
            as_of=(
                max((result.observed_at for result in results), default=datetime.now(UTC))
                if results and all(bool(result.metadata.get("fixture_id")) for result in results)
                else datetime.now(UTC)
            ),
        )
        with self.database.session() as session:
            for cluster in clusters:
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

    def health(self, now: datetime | None = None) -> list[SourceHealth]:
        reference = now or datetime.now(UTC)
        output: list[SourceHealth] = []
        for adapter in self.adapters:
            health = adapter.health
            freshness_at = (
                health.last_success_at
                if adapter.freshness_basis == "fetch"
                else health.newest_record_at
            )
            age = max(0.0, (reference - freshness_at).total_seconds()) if freshness_at else None
            threshold = max(
                2 * health.expected_cadence_seconds,
                health.expected_cadence_seconds + 300,
            )
            status = health.status
            control = self._fixture_controls.get(health.stream_id)
            if control == "fail":
                status = SourceHealthStatus.OFFLINE
            elif control == "empty":
                status = SourceHealthStatus.FIXTURE
            elif control == "stale":
                status = SourceHealthStatus.STALE
                age = float(threshold + 1)
            elif status not in {SourceHealthStatus.FIXTURE, SourceHealthStatus.DISABLED} and (
                age is not None and age > threshold
            ):
                status = SourceHealthStatus.STALE
            output.append(
                health.model_copy(
                    update={
                        "status": status,
                        "age_seconds": age,
                        "record_count": 0 if control == "empty" else health.record_count,
                        "message": "Fixture failure" if control == "fail" else health.message,
                    }
                )
            )
        return output

    def select_fixture(self, name: str) -> None:
        path = (self.settings.fixture_dir / name).resolve()
        if path.parent != self.settings.fixture_dir.resolve() or not path.is_file():
            raise KeyError("Unknown fixture")
        self._fixture_name = name

    def control_fixture(self, stream_id: str, mode: str) -> None:
        if mode not in {"normal", "fail", "empty", "stale"}:
            raise ValueError("mode must be normal, fail, empty or stale")
        if stream_id not in {adapter.stream_id for adapter in self.adapters}:
            raise KeyError("Unknown fixture stream")
        if mode == "normal":
            self._fixture_controls.pop(stream_id, None)
        else:
            self._fixture_controls[stream_id] = mode

    async def run(self) -> None:
        while not self._stop.is_set():
            with self.database.session() as session:
                policy = session.get(SessionPolicyRow, 1)
                enabled = policy is None or policy.auto_update_enabled
            if enabled:
                try:
                    await self.refresh_once()
                except Exception as exc:
                    logger.exception(
                        "Source refresh failed during persistence (%s)", type(exc).__name__
                    )
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
