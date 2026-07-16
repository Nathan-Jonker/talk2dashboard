from __future__ import annotations

import pytest

from talk2dashboard.evidence import EvidenceNotFoundError, EvidenceService


async def test_evidence_lookup_is_cached_and_keeps_bundle_provenance(services, monkeypatch):
    _settings, database, sources, _query, _dashboard = services
    bundle = await sources.initialize_fixture()
    service = EvidenceService(database, ttl_seconds=60)

    result = service.get("p2000:fixture-p2000-001")

    assert result["record"]["record_id"] == "fixture-p2000-001"
    assert bundle in result["bundle_versions"]
    monkeypatch.setattr(
        database,
        "session",
        lambda: (_ for _ in ()).throw(AssertionError("cache miss")),
    )
    assert service.get("p2000:fixture-p2000-001") is result


async def test_evidence_lookup_rejects_unknown_record(services):
    _settings, database, sources, _query, _dashboard = services
    await sources.initialize_fixture()
    service = EvidenceService(database)

    with pytest.raises(EvidenceNotFoundError):
        service.get("p2000:missing")
