from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import select

from talk2dashboard.storage.database import Database
from talk2dashboard.storage.models import ClaimAuditRow, ToolAuditRow

NUMBER_PATTERN = re.compile(r"(?<!\w)(?:\d{1,3}(?:[. ]\d{3})+|\d+)(?:[,.]\d+)?(?!\w)")


def _normalized_number(value: str) -> Decimal | None:
    candidate = value.replace(" ", "")
    if "," in candidate:
        candidate = candidate.replace(".", "").replace(",", ".")
    try:
        return Decimal(candidate)
    except InvalidOperation:
        return None


def audit_numeric_claims(
    database: Database,
    *,
    event_id: str,
    conversation_id: str,
    turn_id: str | None,
    text: str,
) -> list[dict[str, object]]:
    claims = [
        (match.group(0), _normalized_number(match.group(0)))
        for match in NUMBER_PATTERN.finditer(text)
    ]
    if not claims:
        return []
    with database.session() as session:
        statement = select(ToolAuditRow).where(
            ToolAuditRow.conversation_id == conversation_id,
            ToolAuditRow.ok.is_(True),
        )
        if turn_id:
            statement = statement.where(ToolAuditRow.turn_id == turn_id)
        audits = session.scalars(statement).all()
        evidence = [json.loads(row.result_json) for row in audits if row.result_json]
        searchable = json.dumps(evidence, ensure_ascii=True, default=str)
        results = []
        for raw, numeric in claims:
            if numeric is None:
                status = "unknown"
            else:
                variants = {raw, str(numeric), str(numeric).replace(".", ",")}
                status = (
                    "supported" if any(value in searchable for value in variants) else "unsupported"
                )
            row = ClaimAuditRow(
                claim_id=f"claim_{uuid4().hex}",
                conversation_id=conversation_id,
                turn_id=turn_id,
                event_id=event_id,
                claim_text=text,
                numeric_value=raw,
                status=status,
                evidence_json=json.dumps(
                    [audit.audit_id for audit in audits if audit.result_json], ensure_ascii=True
                ),
                created_at=datetime.now(UTC).isoformat(),
            )
            session.add(row)
            results.append({"value": raw, "status": status, "claim_id": row.claim_id})
        return results
