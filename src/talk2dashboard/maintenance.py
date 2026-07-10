from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from talk2dashboard.config import Settings
from talk2dashboard.storage.assets import AssetStore
from talk2dashboard.storage.database import Database
from talk2dashboard.storage.models import (
    AssetRow,
    ConversationEventRow,
    ConversationRow,
    DashboardConfigRow,
    DataHandleRow,
    IncidentClusterRow,
    LatencyEventRow,
    NormalizedRecordRow,
    ProviderCallRow,
    SourceBundleRow,
    SourceSnapshotRow,
    ToolAuditRow,
)


def _affected_rows(result: object) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


class MaintenanceService:
    def __init__(self, settings: Settings, database: Database, assets: AssetStore) -> None:
        self.settings = settings
        self.database = database
        self.assets = assets
        self._stop = asyncio.Event()

    def cleanup(self) -> dict[str, int]:
        now = datetime.now(UTC)
        cutoff = (now - timedelta(days=self.settings.data_retention_days)).isoformat()
        removed = {"bundles": 0, "assets": 0, "audit_rows": 0}
        with self.database.session() as session:
            protected_bundles = set(
                session.scalars(select(DashboardConfigRow.source_bundle_version)).all()
            )
            newest = session.scalars(
                select(SourceBundleRow).order_by(SourceBundleRow.created_at.desc()).limit(3)
            ).all()
            protected_bundles.update(row.bundle_version for row in newest)
            expired_bundles = session.scalars(
                select(SourceBundleRow).where(SourceBundleRow.created_at < cutoff)
            ).all()
            for bundle in expired_bundles:
                if bundle.bundle_version in protected_bundles:
                    continue
                session.execute(
                    delete(DataHandleRow).where(
                        DataHandleRow.source_bundle_version == bundle.bundle_version
                    )
                )
                session.execute(
                    delete(IncidentClusterRow).where(
                        IncidentClusterRow.bundle_version == bundle.bundle_version
                    )
                )
                session.delete(bundle)
                removed["bundles"] += 1
            session.flush()
            retained_snapshot_ids: set[str] = set()
            for raw_ids in session.scalars(select(SourceBundleRow.snapshot_ids_json)).all():
                retained_snapshot_ids.update(json.loads(raw_ids))
            old_snapshots = session.scalars(
                select(SourceSnapshotRow).where(SourceSnapshotRow.ingested_at < cutoff)
            ).all()
            for snapshot in old_snapshots:
                if snapshot.snapshot_id in retained_snapshot_ids:
                    continue
                session.execute(
                    delete(NormalizedRecordRow).where(
                        NormalizedRecordRow.snapshot_id == snapshot.snapshot_id
                    )
                )
                session.delete(snapshot)
            removed["audit_rows"] += _affected_rows(
                session.execute(delete(ToolAuditRow).where(ToolAuditRow.created_at < cutoff))
            )
            removed["audit_rows"] += _affected_rows(
                session.execute(delete(LatencyEventRow).where(LatencyEventRow.wall_clock < cutoff))
            )
            removed["audit_rows"] += _affected_rows(
                session.execute(
                    delete(ConversationEventRow).where(ConversationEventRow.created_at < cutoff)
                )
            )
            removed["audit_rows"] += _affected_rows(
                session.execute(delete(ProviderCallRow).where(ProviderCallRow.created_at < cutoff))
            )
            session.execute(
                delete(DataHandleRow).where(
                    DataHandleRow.expires_at.is_not(None),
                    DataHandleRow.expires_at < now.isoformat(),
                )
            )
            ended = session.scalars(
                select(ConversationRow).where(
                    ConversationRow.ended_at.is_not(None), ConversationRow.ended_at < cutoff
                )
            ).all()
            for conversation in ended:
                session.delete(conversation)
            expired_assets = session.scalars(
                select(AssetRow).where(
                    AssetRow.expires_at.is_not(None), AssetRow.expires_at < now.isoformat()
                )
            ).all()
            for asset in expired_assets:
                path = self.assets.path_for(asset)
                path.unlink(missing_ok=True)
                session.delete(asset)
                removed["assets"] += 1
        return removed

    async def run(self) -> None:
        while not self._stop.is_set():
            self.cleanup()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=86400)
            except TimeoutError:
                continue

    def stop(self) -> None:
        self._stop.set()
