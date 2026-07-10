from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from talk2dashboard.storage.database import Database
from talk2dashboard.storage.models import ProviderCallRow


def record_provider_call(
    database: Database | None,
    *,
    provider: str,
    operation: str,
    status: str,
    duration_ms: float,
    usage: dict | None = None,
) -> None:
    if database is None:
        return
    with database.session() as session:
        session.add(
            ProviderCallRow(
                call_id=f"pcall_{uuid4().hex}",
                provider=provider,
                operation=operation,
                status=status,
                duration_ms=duration_ms,
                usage_json=json.dumps(usage or {}, ensure_ascii=True),
                created_at=datetime.now(UTC).isoformat(),
            )
        )
