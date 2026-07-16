from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete

from talk2dashboard.domain import EphemeralLocationResolution
from talk2dashboard.storage.database import Database
from talk2dashboard.storage.models import EphemeralLocationResolutionRow


class EphemeralLocationStore:
    POLICY_VERSION = "google-geocoding-transient-v1"

    def __init__(self, database: Database) -> None:
        self.database = database

    def put(self, source_text: str, match: dict) -> EphemeralLocationResolution:
        now = datetime.now(UTC)
        location = match.get("location") or {}
        resolution = EphemeralLocationResolution(
            resolution_id=f"locres_{uuid4().hex}",
            input_hash=hashlib.sha256(source_text.strip().lower().encode()).hexdigest(),
            google_place_id=match.get("place_id"),
            display_label=str(match.get("display_label") or source_text),
            latitude=float(location["lat"]),
            longitude=float(location["lng"]),
            requested_at=now,
            expires_at=now + timedelta(minutes=15),
            terms_profile=self.POLICY_VERSION,
        )
        with self.database.session() as session:
            session.add(
                EphemeralLocationResolutionRow(
                    resolution_id=resolution.resolution_id,
                    input_hash=resolution.input_hash,
                    google_place_id=resolution.google_place_id,
                    display_label=resolution.display_label,
                    latitude=resolution.latitude,
                    longitude=resolution.longitude,
                    attribution=resolution.attribution,
                    policy_version=resolution.terms_profile,
                    created_at=resolution.requested_at.isoformat(),
                    expires_at=resolution.expires_at.isoformat(),
                )
            )
        return resolution

    def get(self, resolution_id: str) -> EphemeralLocationResolution:
        now = datetime.now(UTC)
        with self.database.session() as session:
            row = session.get(EphemeralLocationResolutionRow, resolution_id)
            if row is None or datetime.fromisoformat(row.expires_at) <= now:
                raise KeyError("Unknown or expired location resolution")
            return EphemeralLocationResolution(
                resolution_id=row.resolution_id,
                input_hash=row.input_hash,
                google_place_id=row.google_place_id,
                display_label=row.display_label,
                latitude=row.latitude,
                longitude=row.longitude,
                requested_at=datetime.fromisoformat(row.created_at),
                expires_at=datetime.fromisoformat(row.expires_at),
                attribution=row.attribution,
                terms_profile=row.policy_version,
            )

    def cleanup(self, now: datetime | None = None) -> int:
        reference = now or datetime.now(UTC)
        with self.database.session() as session:
            result = session.execute(
                delete(EphemeralLocationResolutionRow).where(
                    EphemeralLocationResolutionRow.expires_at <= reference.isoformat()
                )
            )
            return int(getattr(result, "rowcount", 0) or 0)
