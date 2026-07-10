from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import lxml.etree as etree
from dateutil.parser import isoparse

from talk2dashboard.domain import (
    EventRecord,
    LocationRef,
    MeasurementRecord,
    SourceRef,
    TrustTier,
)
from talk2dashboard.sources.base import AdapterResult, SourceAdapter


def _now() -> datetime:
    return datetime.now(UTC)


def _date(value: Any, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        parsed = isoparse(str(value))
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return fallback


def _id(stream: str, value: Any) -> str:
    return f"{stream}:{hashlib.sha256(str(value).encode()).hexdigest()[:20]}"


def _source_ref(
    stream: str,
    record_id: str,
    owner: str,
    observed_at: datetime,
    ingested_at: datetime,
    source_url: str,
    tier: TrustTier,
) -> SourceRef:
    return SourceRef(
        stream_id=stream,
        record_id=record_id,
        source_url=source_url,
        owner=owner,
        trust_tier=tier,
        observed_at=observed_at,
        ingested_at=ingested_at,
    )


class LuchtmeetnetAdapter(SourceAdapter):
    stream_id = "luchtmeetnet"
    owner = "RIVM / Luchtmeetnet"
    provider = "Luchtmeetnet Open API"
    expected_cadence_seconds = 3600
    base_url = "https://api.luchtmeetnet.nl/open_api"

    def __init__(self) -> None:
        super().__init__()

    async def fetch(self) -> AdapterResult:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            stations_response, measurements_response = (
                await client.get(
                    f"{self.base_url}/stations", params={"page": 1, "order_by": "number"}
                ),
                await client.get(
                    f"{self.base_url}/measurements",
                    params={
                        "page": 1,
                        "order_by": "timestamp_measured",
                        "order_direction": "desc",
                    },
                ),
            )
        stations_response.raise_for_status()
        measurements_response.raise_for_status()
        station_payload = stations_response.json()
        measurement_payload = measurements_response.json()
        stations = {
            str(item.get("number")): item
            for item in station_payload.get("data", [])
            if item.get("number")
        }
        now = _now()
        rows: list[MeasurementRecord] = []
        for item in measurement_payload.get("data", []):
            station_id = str(item.get("station_number") or item.get("station") or "")
            station = stations.get(station_id, {})
            formula = str(item.get("formula") or item.get("component") or "unknown").lower()
            try:
                value = float(item["value"])
            except (KeyError, TypeError, ValueError):
                continue
            observed = _date(item.get("timestamp_measured"), now)
            record_id = _id(self.stream_id, f"{station_id}:{formula}:{observed.isoformat()}")
            coordinates = station.get("geometry", {}).get("coordinates", [])
            location = None
            if len(coordinates) >= 2:
                location = LocationRef(
                    location_id=f"luchtmeetnet:{station_id}",
                    label=str(station.get("location") or station_id),
                    latitude=float(coordinates[1]),
                    longitude=float(coordinates[0]),
                    geometry_source="source",
                )
            source_ref = _source_ref(
                self.stream_id,
                record_id,
                self.owner,
                observed,
                now,
                f"{self.base_url}/measurements",
                TrustTier.OFFICIAL_MEASUREMENT,
            )
            rows.append(
                MeasurementRecord(
                    record_id=record_id,
                    stream_id=self.stream_id,
                    metric=f"{formula}_ug_m3",
                    value=value,
                    unit=str(item.get("unit") or "ug/m3"),
                    observed_at=observed,
                    ingested_at=now,
                    location=location,
                    source_ref=source_ref,
                )
            )
        raw = json.dumps(
            {"stations": station_payload, "measurements": measurement_payload},
            ensure_ascii=True,
        ).encode()
        return AdapterResult(
            stream_id=self.stream_id,
            provider=self.provider,
            source_url=f"{self.base_url}/measurements",
            raw=raw,
            observed_at=max((row.observed_at for row in rows), default=now),
            measurements=rows,
        )


class RWSWaterAdapter(SourceAdapter):
    stream_id = "rws_water"
    owner = "Rijkswaterstaat"
    provider = "RWS DDAPI20 WFS"
    expected_cadence_seconds = 600
    url = "https://geo.rijkswaterstaat.nl/services/ogc/hws/DDAPI20/ows"

    def __init__(self) -> None:
        super().__init__()

    async def fetch(self) -> AdapterResult:
        params = {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": "locatiesmetlaatstewaarneming",
            "COUNT": 500,
            "outputFormat": "application/json",
            "CQL_FILTER": "GROOTHEIDCODE='WATHTE'",
        }
        async with httpx.AsyncClient(timeout=35, follow_redirects=True) as client:
            response = await client.get(self.url, params=params)
        response.raise_for_status()
        payload = response.json()
        now = _now()
        rows: list[MeasurementRecord] = []
        for feature in payload.get("features", []):
            properties = feature.get("properties") or {}
            coordinates = (feature.get("geometry") or {}).get("coordinates") or []
            try:
                value = float(properties["WAARDE_LAATSTE_METING"])
            except (KeyError, TypeError, ValueError):
                continue
            observed = _date(properties.get("TIJDSTIP_LAATSTE_METING"), now)
            station = str(
                properties.get("NAAM")
                or properties.get("LOCATIECODE")
                or feature.get("id")
                or "RWS meetpunt"
            )
            record_id = _id(self.stream_id, f"{station}:{observed.isoformat()}")
            location = None
            if len(coordinates) >= 2:
                location = LocationRef(
                    location_id=f"rws:{hashlib.sha256(station.encode()).hexdigest()[:12]}",
                    label=station,
                    latitude=float(coordinates[1]),
                    longitude=float(coordinates[0]),
                    geometry_source="source",
                )
            source_ref = _source_ref(
                self.stream_id,
                record_id,
                self.owner,
                observed,
                now,
                self.url,
                TrustTier.OFFICIAL_MEASUREMENT,
            )
            quality = []
            if properties.get("KWALITEITSOORDEELCODE") not in {None, "00", 0}:
                quality.append("source_quality_flag")
            rows.append(
                MeasurementRecord(
                    record_id=record_id,
                    stream_id=self.stream_id,
                    metric="water_level_cm",
                    value=value,
                    unit=str(properties.get("EENHEIDCODE") or "cm"),
                    observed_at=observed,
                    ingested_at=now,
                    location=location,
                    source_ref=source_ref,
                    quality_flags=tuple(quality),
                )
            )
        return AdapterResult(
            stream_id=self.stream_id,
            provider=self.provider,
            source_url=self.url,
            raw=response.content,
            observed_at=max((row.observed_at for row in rows), default=now),
            measurements=rows,
        )


class NDWIncidentAdapter(SourceAdapter):
    stream_id = "ndw_incidents"
    owner = "Nationaal Dataportaal Wegverkeer"
    provider = "NDW open data DATEX II"
    expected_cadence_seconds = 60
    url = "https://opendata.ndw.nu/actueel_beeld.xml.gz"

    def __init__(self) -> None:
        super().__init__()

    @staticmethod
    def _first_text(element: etree._Element, names: set[str]) -> str | None:
        for child in element.iter():
            if etree.QName(child).localname in names and child.text and child.text.strip():
                return child.text.strip()
        return None

    def parse(self, raw: bytes, now: datetime | None = None) -> list[EventRecord]:
        now = now or _now()
        document = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
        events: list[EventRecord] = []
        for _, element in etree.iterparse(io.BytesIO(document), events=("end",)):
            if etree.QName(element).localname != "situationRecord":
                continue
            external_id = element.get("id") or self._first_text(element, {"id"}) or len(events)
            record_id = _id(self.stream_id, external_id)
            observed = _date(
                self._first_text(element, {"overallStartTime", "situationRecordCreationTime"}),
                now,
            )
            title = self._first_text(element, {"value", "description"}) or "Verkeersincident"
            category = self._first_text(element, {"situationRecordType", "accidentType"})
            category = category or etree.QName(element).localname
            latitude = self._first_text(element, {"latitude"})
            longitude = self._first_text(element, {"longitude"})
            location = None
            if latitude and longitude:
                with contextlib.suppress(ValueError):
                    location = LocationRef(
                        location_id=f"ndw:{record_id}",
                        label=title[:120],
                        latitude=float(latitude),
                        longitude=float(longitude),
                        uncertainty_radius_m=250,
                        geometry_source="source",
                    )
            source_ref = _source_ref(
                self.stream_id,
                record_id,
                self.owner,
                observed,
                now,
                self.url,
                TrustTier.OFFICIAL_OPERATIONAL,
            )
            events.append(
                EventRecord(
                    record_id=record_id,
                    stream_id=self.stream_id,
                    category=str(category)[:100],
                    title=title[:300],
                    observed_at=observed,
                    ingested_at=now,
                    location=location,
                    source_ref=source_ref,
                    attributes={"external_id": str(external_id)},
                )
            )
            element.clear()
        return events[:1000]

    async def fetch(self) -> AdapterResult:
        async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
            response = await client.get(self.url, headers={"User-Agent": "Talk2Dashboard/0.1"})
        response.raise_for_status()
        events = self.parse(response.content)
        now = _now()
        return AdapterResult(
            stream_id=self.stream_id,
            provider=self.provider,
            source_url=self.url,
            raw=response.content,
            observed_at=max((row.observed_at for row in events), default=now),
            events=events,
        )


class NSDisruptionsAdapter(SourceAdapter):
    stream_id = "ns_disruptions"
    owner = "Nederlandse Spoorwegen"
    provider = "NS Reisinformatie API"
    expected_cadence_seconds = 60
    url = "https://gateway.apiportal.ns.nl/reisinformatie-api/api/v3/disruptions"

    def __init__(self, subscription_key: str) -> None:
        self.subscription_key = subscription_key
        super().__init__()

    async def fetch(self) -> AdapterResult:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.get(
                self.url,
                params={"isActive": "true"},
                headers={"Ocp-Apim-Subscription-Key": self.subscription_key},
            )
        response.raise_for_status()
        payload = response.json()
        now = _now()
        items = payload if isinstance(payload, list) else payload.get("disruptions", [])
        events: list[EventRecord] = []
        for item in items:
            external_id = item.get("id") or item.get("code") or json.dumps(item, sort_keys=True)
            record_id = _id(self.stream_id, external_id)
            observed = _date(item.get("start") or item.get("registrationTime"), now)
            title = item.get("title") or item.get("type") or "NS verstoring"
            source_ref = _source_ref(
                self.stream_id,
                record_id,
                self.owner,
                observed,
                now,
                self.url,
                TrustTier.OFFICIAL_OPERATIONAL,
            )
            events.append(
                EventRecord(
                    record_id=record_id,
                    stream_id=self.stream_id,
                    category=str(item.get("type") or "rail_disruption"),
                    title=str(title)[:300],
                    description=str(item.get("description") or item.get("message") or "")[:1000]
                    or None,
                    severity="high" if item.get("isActive", True) else "low",
                    status="active" if item.get("isActive", True) else "resolved",
                    observed_at=observed,
                    ingested_at=now,
                    source_ref=source_ref,
                    attributes={"external_id": str(external_id)},
                )
            )
        return AdapterResult(
            stream_id=self.stream_id,
            provider=self.provider,
            source_url=self.url,
            raw=response.content,
            observed_at=max((row.observed_at for row in events), default=now),
            events=events,
        )


class Radar112Adapter(SourceAdapter):
    stream_id = "p2000"
    owner = "112Radar"
    provider = "112Radar REST API"
    expected_cadence_seconds = 60
    url = "https://112radar.nl/api/v1/incidents"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        super().__init__()

    async def fetch(self) -> AdapterResult:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.get(
                self.url, headers={"Authorization": f"Bearer {self.api_key}"}
            )
        response.raise_for_status()
        payload = response.json()
        now = _now()
        items = payload.get("data", payload) if isinstance(payload, dict) else payload
        events: list[EventRecord] = []
        for item in items[:250]:
            external_id = item.get("id") or item.get("capcode") or json.dumps(item, sort_keys=True)
            record_id = _id(self.stream_id, external_id)
            observed = _date(item.get("timestamp") or item.get("created_at"), now)
            lat, lon = item.get("latitude"), item.get("longitude")
            location = None
            if lat is not None and lon is not None:
                location = LocationRef(
                    location_id=f"p2000:{record_id}",
                    label=str(item.get("location") or item.get("city") or "P2000 locatie"),
                    latitude=float(lat),
                    longitude=float(lon),
                    uncertainty_radius_m=750,
                    geometry_source="source",
                )
            source_ref = _source_ref(
                self.stream_id,
                record_id,
                self.owner,
                observed,
                now,
                self.url,
                TrustTier.SIGNAL,
            )
            events.append(
                EventRecord(
                    record_id=record_id,
                    stream_id=self.stream_id,
                    category=str(item.get("discipline") or item.get("category") or "p2000_signal"),
                    title=str(item.get("message") or item.get("title") or "P2000 melding")[:300],
                    observed_at=observed,
                    ingested_at=now,
                    location=location,
                    source_ref=source_ref,
                    attributes={"provider": "112Radar", "external_id": str(external_id)},
                )
            )
        return AdapterResult(
            stream_id=self.stream_id,
            provider=self.provider,
            source_url=self.url,
            raw=response.content,
            observed_at=max((row.observed_at for row in events), default=now),
            events=events,
        )
