from __future__ import annotations

import json
import time
from collections import OrderedDict
from threading import RLock
from typing import Any

from sqlalchemy import select

from talk2dashboard.storage.database import Database
from talk2dashboard.storage.models import NormalizedRecordRow, SourceBundleRow, SourceSnapshotRow


class EvidenceNotFoundError(KeyError):
    pass


class EvidenceService:
    """Fast provenance lookup with a small cache aligned to source refresh cadence."""

    def __init__(
        self, database: Database, *, ttl_seconds: int = 60, max_entries: int = 512
    ) -> None:
        self.database = database
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = RLock()

    def get(self, source_ref: str) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(source_ref)
            if cached and cached[0] > now:
                self._cache.move_to_end(source_ref)
                return cached[1]
            self._cache.pop(source_ref, None)

        stream_id, record_id = source_ref.split(":", 1)
        with self.database.session() as session:
            record = session.scalars(
                select(NormalizedRecordRow)
                .where(
                    NormalizedRecordRow.stream_id == stream_id,
                    NormalizedRecordRow.record_id == record_id,
                )
                .order_by(NormalizedRecordRow.observed_at.desc())
                .limit(1)
            ).first()
            if record is None:
                raise EvidenceNotFoundError(source_ref)
            snapshot = session.get(SourceSnapshotRow, record.snapshot_id)
            if snapshot is None:
                raise EvidenceNotFoundError(source_ref)
            snapshot_token = f'%"{record.snapshot_id}"%'
            bundle_ids = list(
                session.scalars(
                    select(SourceBundleRow.bundle_version).where(
                        SourceBundleRow.snapshot_ids_json.like(snapshot_token)
                    )
                ).all()
            )
            normalized = json.loads(record.payload_json)
            metadata = json.loads(snapshot.metadata_json)
            ref = normalized.get("source_ref") or {}
            result = {
                "source_ref": source_ref,
                "record": normalized,
                "snapshot": {
                    "snapshot_id": snapshot.snapshot_id,
                    "content_hash": snapshot.content_hash,
                    "source_url": snapshot.source_url,
                    "provider": snapshot.provider,
                    "observed_at": snapshot.observed_at,
                    "ingested_at": snapshot.ingested_at,
                    "metadata": metadata,
                },
                "owner": ref.get("owner"),
                "trust_tier": ref.get("trust_tier"),
                "quality_flags": normalized.get("quality_flags", []),
                "bundle_versions": bundle_ids,
                "fallback": {
                    "used": bool(metadata.get("fallback_from")),
                    "from": metadata.get("fallback_from"),
                    "reason": metadata.get("fallback_reason"),
                },
            }

        with self._lock:
            self._cache[source_ref] = (now + self.ttl_seconds, result)
            self._cache.move_to_end(source_ref)
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)
        return result
