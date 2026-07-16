from __future__ import annotations

import asyncio
import contextlib
import gzip
import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

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
        self._station_cache: dict[str, dict[str, Any]] = {}
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
        stations: dict[str, dict[str, Any]] = {
            str(item.get("number")): item
            for item in station_payload.get("data", [])
            if item.get("number")
        }
        for station_id, station in stations.items():
            if len((station.get("geometry") or {}).get("coordinates") or []) >= 2:
                self._station_cache[station_id] = station
        station_ids = {
            str(item.get("station_number") or item.get("station") or "")
            for item in measurement_payload.get("data", [])
            if item.get("station_number") or item.get("station")
        }
        missing_station_ids = sorted(station_ids - self._station_cache.keys())
        semaphore = asyncio.Semaphore(12)

        if missing_station_ids:
            async with httpx.AsyncClient(timeout=25, follow_redirects=True) as detail_client:

                async def load_station(station_id: str) -> None:
                    async with semaphore:
                        try:
                            response = await detail_client.get(
                                f"{self.base_url}/stations/{station_id}"
                            )
                            response.raise_for_status()
                            detail = response.json().get("data") or {}
                        except (httpx.HTTPError, ValueError):
                            return
                        if isinstance(detail, dict):
                            self._station_cache[station_id] = {"number": station_id, **detail}

                await asyncio.gather(
                    *(load_station(station_id) for station_id in missing_station_ids)
                )
        stations.update(self._station_cache)
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
            metadata={
                "station_count": len(station_ids),
                "stations_with_coordinates": sum(row.location is not None for row in rows),
            },
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
        original_count = len(rows)
        if rows:
            newest = max(row.observed_at for row in rows)
            cutoff = newest - timedelta(hours=24)
            rows = [row for row in rows if row.observed_at >= cutoff]
        return AdapterResult(
            stream_id=self.stream_id,
            provider=self.provider,
            source_url=self.url,
            raw=response.content,
            observed_at=max((row.observed_at for row in rows), default=now),
            measurements=rows,
            metadata={"discarded_non_current_records": original_count - len(rows)},
        )


class NDWIncidentAdapter(SourceAdapter):
    stream_id = "ndw_incidents"
    owner = "Nationaal Dataportaal Wegverkeer"
    provider = "NDW open data DATEX II"
    expected_cadence_seconds = 60
    freshness_basis = "fetch"
    url = "https://opendata.ndw.nu/actueel_beeld.xml.gz"

    _titles: ClassVar[dict[str, str]] = {
        "Accident": "Ongeval op de weg",
        "AbnormalTraffic": "Verkeersopstopping",
        "GeneralNetworkManagement": "Verkeersmaatregel actief",
        "GeneralObstruction": "Obstakel op de rijbaan",
        "ReroutingManagement": "Omleiding ingesteld",
        "RoadOrCarriagewayOrLaneManagement": "Rijstrookmaatregel actief",
        "SpeedManagement": "Tijdelijke snelheidsbeperking",
        "VehicleObstruction": "Stilstaand voertuig of object",
    }
    _specific_titles: ClassVar[dict[str, str]] = {
        "bridgeSwingInOperation": "Brug geopend voor scheepvaart",
        "laneClosures": "Rijstrook afgesloten",
        "followDiversionSigns": "Omleiding via verkeersborden",
        "stationaryTraffic": "Stilstaand verkeer",
        "slowTraffic": "Langzaam rijdend verkeer",
        "speedRestrictionInOperation": "Tijdelijke snelheidsbeperking",
    }
    _causes: ClassVar[dict[str, str]] = {
        "roadMaintenance": "wegwerkzaamheden",
        "constructionWork": "bouwwerkzaamheden",
        "accident": "een ongeval",
        "poorWeather": "slecht weer",
        "other": "een operationele melding",
    }
    _carriageways: ClassVar[dict[str, str]] = {
        "mainCarriageway": "hoofdrijbaan",
        "entrySlipRoad": "oprit",
        "exitSlipRoad": "afrit",
        "parallelCarriageway": "parallelrijbaan",
    }

    def __init__(self) -> None:
        super().__init__()

    @staticmethod
    def _first_text(element: etree._Element, names: set[str]) -> str | None:
        for child in element.iter():
            if etree.QName(child).localname in names and child.text and child.text.strip():
                return child.text.strip()
        return None

    @staticmethod
    def _nested_value(element: etree._Element, container_names: set[str]) -> str | None:
        for container in element.iter():
            if etree.QName(container).localname not in container_names:
                continue
            for child in container.iter():
                if etree.QName(child).localname == "value" and child.text and child.text.strip():
                    return child.text.strip()
        return None

    @staticmethod
    def _record_type(element: etree._Element) -> str:
        xsi_type = element.get("{http://www.w3.org/2001/XMLSchema-instance}type") or ""
        return xsi_type.rsplit(":", 1)[-1] or "TrafficSituation"

    @classmethod
    def _human_text(cls, element: etree._Element) -> tuple[str, str]:
        record_type = cls._record_type(element)
        explicit = cls._nested_value(element, {"description", "comment"})
        detail_codes = [
            cls._first_text(
                element,
                {
                    "abnormalTrafficType",
                    "generalNetworkManagementType",
                    "roadOrCarriagewayOrLaneManagementType",
                    "reroutingManagementType",
                    "speedManagementType",
                },
            )
        ]
        title = explicit or next(
            (cls._specific_titles[code] for code in detail_codes if code in cls._specific_titles),
            cls._titles.get(record_type, "Actuele verkeersmelding"),
        )

        details: list[str] = []
        cause = cls._first_text(element, {"causeType"})
        if cause:
            details.append(f"Vanwege {cls._causes.get(cause, cause)}")
        carriageway = cls._first_text(element, {"carriageway"})
        if carriageway in cls._carriageways:
            details.append(f"op de {cls._carriageways[carriageway]}")
        temporary_limit = cls._first_text(element, {"temporarySpeedLimit"})
        if temporary_limit:
            with contextlib.suppress(ValueError):
                details.append(f"maximaal {float(temporary_limit):g} kilometer per uur")
        restricted = cls._first_text(element, {"numberOfLanesRestricted"})
        operational = cls._first_text(element, {"numberOfOperationalLanes"})
        if restricted:
            details.append(
                f"{restricted} rijstrook beperkt"
                + (f" en {operational} beschikbaar" if operational else "")
            )
        delay = cls._first_text(element, {"delayTimeValue"})
        if delay:
            with contextlib.suppress(ValueError):
                details.append(f"circa {max(1, round(float(delay) / 60))} minuten vertraging")
        queue = cls._first_text(element, {"queueLength"})
        if queue:
            with contextlib.suppress(ValueError):
                details.append(f"filelengte circa {float(queue) / 1000:.1f} kilometer")
        rerouting = cls._nested_value(element, {"reroutingManagementType"})
        if rerouting and rerouting.casefold() not in title.casefold():
            details.append(rerouting)
        if not details:
            details.append("Actuele veiligheidsmelding uit de officiële NDW-verkeersfeed")
        return title, "; ".join(details) + "."

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
            title, description = self._human_text(element)
            category = self._record_type(element)
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
                    description=description[:500],
                    observed_at=observed,
                    ingested_at=now,
                    location=location,
                    source_ref=source_ref,
                    attributes={"external_id": str(external_id), "datex_type": category},
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
            metadata={"descriptions": "humanized-v1"},
        )


class NSDisruptionsAdapter(SourceAdapter):
    stream_id = "ns_disruptions"
    owner = "Nederlandse Spoorwegen"
    provider = "NS Reisinformatie API"
    expected_cadence_seconds = 60
    freshness_basis = "fetch"
    url = "https://gateway.apiportal.ns.nl/reisinformatie-api/api/v3/disruptions"
    stations_url = "https://gateway.apiportal.ns.nl/reisinformatie-api/api/v2/stations"

    def __init__(self, subscription_key: str) -> None:
        self.subscription_key = subscription_key
        super().__init__()

    @staticmethod
    def _station_catalog(payload: Any) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        items = payload if isinstance(payload, list) else payload.get("payload", [])
        aliases: dict[str, dict[str, Any]] = {}
        stations: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            names = item.get("namen") or item.get("names") or {}
            label = (
                names.get("lang")
                or names.get("middel")
                or names.get("kort")
                or item.get("name")
            )
            latitude = item.get("lat", item.get("latitude"))
            longitude = item.get("lng", item.get("longitude"))
            if not label or latitude is None or longitude is None:
                continue
            code = str(item.get("code") or item.get("stationCode") or "").strip()
            uic = str(item.get("UICCode") or item.get("uicCode") or "").strip()
            station = {
                "code": code,
                "uic": uic,
                "label": str(label),
                "latitude": float(latitude),
                "longitude": float(longitude),
            }
            stations.append(station)
            for alias in (code, uic, str(label)):
                if alias:
                    aliases[alias.casefold()] = station
        return aliases, stations

    @staticmethod
    def _station_refs(
        item: dict[str, Any],
        aliases: dict[str, dict[str, Any]],
        stations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        station_keys = {
            "station",
            "stations",
            "stationcode",
            "stationcodes",
            "uiccode",
            "uiccodes",
            "fromstation",
            "tostation",
        }

        def visit(value: Any, parent_key: str = "") -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    visit(nested, str(key).replace("_", "").casefold())
                return
            if isinstance(value, list):
                for nested in value:
                    visit(nested, parent_key)
                return
            if parent_key not in station_keys or value is None:
                return
            station = aliases.get(str(value).strip().casefold())
            if station:
                found[station["code"] or station["uic"] or station["label"]] = station

        visit(item)
        if not found:
            searchable = json.dumps(item, ensure_ascii=False).casefold()
            for station in stations:
                if station["label"].casefold() in searchable:
                    found[station["code"] or station["uic"] or station["label"]] = station
        return list(found.values())

    async def fetch(self) -> AdapterResult:
        async with httpx.AsyncClient(timeout=25) as client:
            headers = {"Ocp-Apim-Subscription-Key": self.subscription_key}
            response, stations_response = await asyncio.gather(
                client.get(self.url, params={"isActive": "true"}, headers=headers),
                client.get(self.stations_url, headers=headers),
                return_exceptions=True,
            )
        if isinstance(response, BaseException):
            raise response
        response.raise_for_status()
        payload = response.json()
        station_aliases: dict[str, dict[str, Any]] = {}
        stations: list[dict[str, Any]] = []
        if isinstance(stations_response, httpx.Response) and stations_response.is_success:
            station_aliases, stations = self._station_catalog(stations_response.json())
        now = _now()
        items = payload if isinstance(payload, list) else payload.get("disruptions", [])
        events: list[EventRecord] = []
        disruptions_without_station = 0
        for item in items:
            external_id = item.get("id") or item.get("code") or json.dumps(item, sort_keys=True)
            observed = _date(item.get("start") or item.get("registrationTime"), now)
            title = item.get("title") or item.get("type") or "NS verstoring"
            matched_stations = self._station_refs(item, station_aliases, stations)
            if not matched_stations:
                disruptions_without_station += 1
                matched_stations = [None]
            for station in matched_stations:
                station_key = station["code"] if station else "unlocated"
                record_id = _id(self.stream_id, f"{external_id}:{station_key}")
                source_ref = _source_ref(
                    self.stream_id,
                    record_id,
                    self.owner,
                    observed,
                    now,
                    self.url,
                    TrustTier.OFFICIAL_OPERATIONAL,
                )
                location = (
                    LocationRef(
                        location_id=_id("ns_station", station_key),
                        label=station["label"],
                        latitude=station["latitude"],
                        longitude=station["longitude"],
                        geometry_source="source",
                        source_refs=(source_ref,),
                    )
                    if station
                    else None
                )
                attributes: dict[str, str | int | float | bool | None] = {
                    "external_id": str(external_id)
                }
                if station:
                    attributes.update(
                        {
                            "station_code": station["code"],
                            "station_uic": station["uic"],
                            "station_name": station["label"],
                        }
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
                        location=location,
                        source_ref=source_ref,
                        attributes=attributes,
                    )
                )
        return AdapterResult(
            stream_id=self.stream_id,
            provider=self.provider,
            source_url=self.url,
            raw=response.content,
            observed_at=max((row.observed_at for row in events), default=now),
            events=events,
            metadata={
                "stations_with_coordinates": len(stations),
                "disruptions_without_station": disruptions_without_station,
            },
        )


class Radar112Adapter(SourceAdapter):
    stream_id = "p2000"
    owner = "112Radar"
    provider = "112Radar REST API"
    expected_cadence_seconds = 60
    freshness_basis = "fetch"
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
