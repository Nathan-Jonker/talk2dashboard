from __future__ import annotations

import re
from typing import Any

from talk2dashboard.evidence import EvidenceService
from talk2dashboard.integrations.places import GeocodingClient
from talk2dashboard.locations import EphemeralLocationStore


class ContextLocationNotResolvedError(ValueError):
    pass


def _location_query(source_ref: str, record: dict[str, Any]) -> str:
    title = str(record.get("title") or "").strip()
    description = str(record.get("description") or "").strip()
    if source_ref.startswith("p2000:"):
        for text in (description, title):
            match = re.search(r"\b(?:op|aan|naar|in)\s+(.+)$", text, flags=re.IGNORECASE)
            if match:
                candidate = re.sub(r"\s+\d{6}\s*$", "", match.group(1)).strip(" .,-")
                if candidate:
                    return f"{candidate}, Nederland"
    return " ".join(part for part in (title, description) if part)


class ContextLocationService:
    """Resolve a selected immutable record to temporary map coordinates."""

    def __init__(
        self,
        evidence: EvidenceService,
        geocoding: GeocodingClient,
        locations: EphemeralLocationStore,
    ) -> None:
        self.evidence = evidence
        self.geocoding = geocoding
        self.locations = locations

    async def resolve(self, source_ref: str) -> dict[str, Any]:
        evidence = self.evidence.get(source_ref)
        record = evidence["record"]
        location = record.get("location") or {}
        if location.get("latitude") is not None and location.get("longitude") is not None:
            return {
                "source_ref": source_ref,
                "latitude": float(location["latitude"]),
                "longitude": float(location["longitude"]),
                "label": str(location.get("label") or record.get("title") or source_ref),
                "location_source": "source",
                "resolution_id": None,
                "expires_at": None,
            }

        query_text = _location_query(source_ref, record)
        if not query_text:
            raise ContextLocationNotResolvedError(
                "Bronrecord bevat geen bruikbare locatieomschrijving."
            )

        resolution = self.locations.find_active(query_text)
        if resolution is None:
            result = await self.geocoding.resolve(query_text)
            matches = result.get("matches") or []
            if not matches:
                raise ContextLocationNotResolvedError(
                    "De locatie uit het bronrecord kon niet worden gevonden."
                )
            resolution = self.locations.put(query_text, matches[0])

        return {
            "source_ref": source_ref,
            "latitude": resolution.latitude,
            "longitude": resolution.longitude,
            "label": resolution.display_label,
            "location_source": "geocoded",
            "resolution_id": resolution.resolution_id,
            "expires_at": resolution.expires_at.isoformat(),
            "attribution": resolution.attribution,
        }
