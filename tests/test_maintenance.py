from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from talk2dashboard.maintenance import MaintenanceService
from talk2dashboard.storage.assets import AssetStore
from talk2dashboard.storage.models import AssetRow, SourceBundleRow


async def test_retention_keeps_referenced_and_three_newest_bundles(services) -> None:
    settings, database, sources, _query, dashboard = services
    bundles = [await sources.initialize_fixture() for _ in range(5)]
    dashboard.ensure_default(bundles[0])
    old = datetime.now(UTC) - timedelta(days=settings.data_retention_days + 5)
    with database.session() as session:
        for index, bundle_id in enumerate(bundles):
            row = session.get(SourceBundleRow, bundle_id)
            assert row is not None
            row.created_at = (old + timedelta(minutes=index)).isoformat()

    assets = AssetStore(settings.asset_dir, database)
    asset_handle = assets.put(b"expired", media_type="text/plain", suffix="txt", ttl_seconds=60)
    with database.session() as session:
        asset = session.get(AssetRow, asset_handle)
        assert asset is not None
        asset.expires_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()

    result = MaintenanceService(settings, database, assets).cleanup()
    with database.session() as session:
        remaining = set(session.scalars(select(SourceBundleRow.bundle_version)).all())
        expired_asset = session.get(AssetRow, asset_handle)
    assert bundles[0] in remaining
    assert set(bundles[-3:]) <= remaining
    assert result["bundles"] == 1
    assert result["assets"] == 1
    assert expired_asset is None
