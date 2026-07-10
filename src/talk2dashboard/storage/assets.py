from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from talk2dashboard.storage.database import Database
from talk2dashboard.storage.models import AssetRow


class AssetStore:
    def __init__(self, root: Path, database: Database) -> None:
        self.root = root
        self.database = database
        self.root.mkdir(parents=True, exist_ok=True)

    def put(
        self,
        content: bytes,
        *,
        media_type: str,
        suffix: str,
        metadata: dict | None = None,
        ttl_seconds: int | None = None,
    ) -> str:
        digest = hashlib.sha256(content).hexdigest()
        path = self.root / digest[:2] / f"{digest}.{suffix.lstrip('.')}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
        now = datetime.now(UTC)
        handle = f"asset_{uuid4().hex}"
        with self.database.session() as session:
            existing = session.query(AssetRow).filter_by(content_hash=digest).first()
            if existing:
                return existing.asset_handle
            session.add(
                AssetRow(
                    asset_handle=handle,
                    content_hash=digest,
                    media_type=media_type,
                    suffix=suffix.lstrip("."),
                    size_bytes=len(content),
                    created_at=now.isoformat(),
                    expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat()
                    if ttl_seconds
                    else None,
                    metadata_json=json.dumps(metadata or {}, ensure_ascii=True),
                )
            )
        return handle

    def resolve(self, handle: str) -> tuple[Path, AssetRow] | None:
        with self.database.session() as session:
            row = session.get(AssetRow, handle)
            if row is None:
                return None
            path = self.path_for(row)
            return (path, row) if path.exists() else None

    def path_for(self, row: AssetRow) -> Path:
        return self.root / row.content_hash[:2] / f"{row.content_hash}.{row.suffix}"
