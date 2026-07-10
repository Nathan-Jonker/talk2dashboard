from __future__ import annotations

from pathlib import Path

import pytest

from talk2dashboard.config import ROOT, Settings
from talk2dashboard.dashboard import DashboardService
from talk2dashboard.query import QueryEngine
from talk2dashboard.sources.service import SourceService
from talk2dashboard.storage.database import Database


@pytest.fixture
def services(tmp_path: Path):
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_path=tmp_path / "test.sqlite3",
        asset_dir=tmp_path / "assets",
        raw_dir=tmp_path / "raw",
        fixture_dir=ROOT / "data/fixtures",
        p2000_provider_mode="disabled",
    )
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    sources = SourceService(settings, database)
    query = QueryEngine(database)
    dashboard = DashboardService(database, query)
    return settings, database, sources, query, dashboard
