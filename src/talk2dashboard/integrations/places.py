from __future__ import annotations

import time
from datetime import UTC, date, datetime
from typing import ClassVar

import httpx

from talk2dashboard.agent_catalog import NEARBY_PLACE_TYPES
from talk2dashboard.config import Settings
from talk2dashboard.integrations.audit import record_provider_call
from talk2dashboard.storage.database import Database
from talk2dashboard.storage.models import BudgetCounterRow


class BudgetExceededError(RuntimeError):
    pass


class DailyBudget:
    def __init__(self, database: Database) -> None:
        self.database = database

    def consume(self, key: str, limit: int) -> tuple[int, int]:
        today = date.today().isoformat()
        with self.database.session() as session:
            row = session.get(BudgetCounterRow, {"counter_key": key, "day": today})
            count = row.count if row else 0
            if count >= limit:
                raise BudgetExceededError(f"{key.upper()}_DAILY_BUDGET_REACHED")
            if row:
                row.count += 1
                row.updated_at = datetime.now(UTC).isoformat()
            else:
                row = BudgetCounterRow(
                    counter_key=key,
                    day=today,
                    count=1,
                    updated_at=datetime.now(UTC).isoformat(),
                )
                session.add(row)
            return row.count, limit


class PlacesClient:
    ENDPOINT = "https://places.googleapis.com/v1/places:searchNearby"

    FIELD_PROFILES: ClassVar[dict[str, str]] = {
        "minimal": "places.id,places.displayName,places.primaryType,places.location,places.googleMapsUri",
        "contact": "places.id,places.displayName,places.primaryType,places.location,places.googleMapsUri,places.formattedAddress,places.nationalPhoneNumber",
        "operational": "places.id,places.displayName,places.primaryType,places.location,places.googleMapsUri,places.regularOpeningHours",
    }
    ALLOWED_TYPES: ClassVar[set[str]] = set(NEARBY_PLACE_TYPES)
    PRIMARY_TYPE_COMPATIBILITY: ClassVar[dict[str, set[str]]] = {
        "transit_station": {"transit_station", "train_station", "bus_station"},
    }

    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self.budget = DailyBudget(database)

    async def nearby(
        self,
        *,
        latitude: float,
        longitude: float,
        included_types: list[str],
        radius_m: int,
        max_results: int,
        rank: str,
        fields_profile: str,
    ) -> dict:
        if not 0 < radius_m <= 25_000:
            raise ValueError("radius_m must be between 1 and 25000")
        if not 1 <= max_results <= 15:
            raise ValueError("max_results must be between 1 and 15")
        unknown_types = sorted(set(included_types) - self.ALLOWED_TYPES)
        if unknown_types:
            raise ValueError(f"unsupported Place types: {', '.join(unknown_types)}")
        if not self.settings.google_places_server_api_key:
            raise RuntimeError("GOOGLE_PLACES_NOT_CONFIGURED")
        count, limit = self.budget.consume("places", self.settings.places_daily_budget)
        field_mask = self.FIELD_PROFILES.get(fields_profile)
        if not field_mask:
            raise ValueError("unknown fields_profile")
        payload = {
            "includedPrimaryTypes": included_types,
            "maxResultCount": max_results,
            "rankPreference": "DISTANCE" if rank == "distance" else "POPULARITY",
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": latitude, "longitude": longitude},
                    "radius": float(radius_m),
                }
            },
            "languageCode": "nl",
            "regionCode": "NL",
        }
        started = time.monotonic_ns()
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    self.ENDPOINT,
                    json=payload,
                    headers={
                        "X-Goog-Api-Key": self.settings.google_places_server_api_key,
                        "X-Goog-FieldMask": field_mask,
                    },
                )
                response.raise_for_status()
                data = response.json()
            record_provider_call(
                self.database,
                provider="google_places",
                operation="nearby_search",
                status="success",
                duration_ms=(time.monotonic_ns() - started) / 1_000_000,
                usage={"field_mask": field_mask, "result_count": len(data.get("places", []))},
            )
        except Exception:
            record_provider_call(
                self.database,
                provider="google_places",
                operation="nearby_search",
                status="error",
                duration_ms=(time.monotonic_ns() - started) / 1_000_000,
            )
            raise
        accepted_primary_types = set().union(
            *(
                self.PRIMARY_TYPE_COMPATIBILITY.get(place_type, {place_type})
                for place_type in included_types
            )
        )
        places = [
            item
            for item in data.get("places", [])
            if item.get("primaryType") in accepted_primary_types
        ]
        return {
            "places": places,
            "attribution": "Google Maps",
            "budget": {"used_today": count, "limit": limit, "warning": count >= int(limit * 0.8)},
            "request": {
                "radius_m": radius_m,
                "included_types": included_types,
                "accepted_primary_types": sorted(accepted_primary_types),
                "field_mask": field_mask,
            },
        }


class GeocodingClient:
    ENDPOINT = "https://maps.googleapis.com/maps/api/geocode/json"

    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self.budget = DailyBudget(database)

    async def resolve(self, text: str) -> dict:
        if not self.settings.google_geocoding_server_api_key:
            raise RuntimeError("GOOGLE_GEOCODING_NOT_CONFIGURED")
        count, limit = self.budget.consume("geocoding", self.settings.geocoding_daily_budget)
        started = time.monotonic_ns()
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                response = await client.get(
                    self.ENDPOINT,
                    params={
                        "address": text,
                        "region": "nl",
                        "language": "nl",
                        "key": self.settings.google_geocoding_server_api_key,
                    },
                )
                response.raise_for_status()
                data = response.json()
            record_provider_call(
                self.database,
                provider="google_geocoding",
                operation="resolve_location",
                status="success",
                duration_ms=(time.monotonic_ns() - started) / 1_000_000,
                usage={"result_count": len(data.get("results", []))},
            )
        except Exception:
            record_provider_call(
                self.database,
                provider="google_geocoding",
                operation="resolve_location",
                status="error",
                duration_ms=(time.monotonic_ns() - started) / 1_000_000,
            )
            raise
        results = data.get("results") or []
        if not results:
            return {"matches": [], "budget": {"used_today": count, "limit": limit}}
        return {
            "matches": [
                {
                    "place_id": item.get("place_id"),
                    "display_label": item.get("formatted_address"),
                    "location": item.get("geometry", {}).get("location"),
                    "temporary": True,
                    "attribution": "Google Maps",
                }
                for item in results[:5]
            ],
            "budget": {"used_today": count, "limit": limit},
        }
