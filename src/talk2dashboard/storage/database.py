from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from talk2dashboard.storage.models import Base


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        event.listen(self.engine, "connect", self._configure_sqlite)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    @staticmethod
    def _configure_sqlite(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)
        self._apply_compatibility_columns()

    def _apply_compatibility_columns(self) -> None:
        """Keep local pre-release databases usable while the schema is still evolving."""
        additions = {
            "dashboard_configs": {
                "logical_binding_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "last_rendered_source_bundle_version": "VARCHAR",
                "last_rendered_handle_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "screenshot_handle": "VARCHAR",
            },
            "tool_audit": {
                "created_at": "VARCHAR NOT NULL DEFAULT ''",
            },
        }
        with self.engine.begin() as connection:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            for table, columns in additions.items():
                if table not in tables:
                    continue
                existing = {column["name"] for column in inspector.get_columns(table)}
                for name, ddl in columns.items():
                    if name not in existing:
                        connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {ddl}'))

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path}"
