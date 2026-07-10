from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from talk2dashboard.config import ROOT
from talk2dashboard.storage.models import Base


def test_static_baseline_migrates_an_empty_database(tmp_path: Path) -> None:
    database_path = tmp_path / "migrated.sqlite3"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

    command.upgrade(config, "head")

    tables = set(inspect(create_engine(f"sqlite:///{database_path}")).get_table_names())
    assert set(Base.metadata.tables) <= tables
    assert "alembic_version" in tables
