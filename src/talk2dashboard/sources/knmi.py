from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx
import numpy as np
import xarray as xr

from talk2dashboard.domain import (
    LocationRef,
    MeasurementRecord,
    SourceRef,
    TrustTier,
)
from talk2dashboard.sources.base import AdapterResult, SourceAdapter


class KNMIObservationsAdapter(SourceAdapter):
    stream_id = "knmi_observations"
    owner = "Koninklijk Nederlands Meteorologisch Instituut"
    provider = "KNMI Open Data"
    expected_cadence_seconds = 600
    dataset = "10-minute-in-situ-meteorological-observations"
    version = "1.0"
    base_url = "https://api.dataplatform.knmi.nl/open-data/v1"

    _metrics: ClassVar[dict[str, tuple[tuple[str, ...], str, float]]] = {
        "wind_gust_kmh": (
            ("wind_speed_of_gust", "fx", "gff", "fxs", "gffs"),
            "km/h",
            3.6,
        ),
        "wind_speed_ms": (("wind_speed", "ff", "ffs"), "m/s", 1.0),
        "rainfall_rate_mm_h": (("rainfall_rate", "rg"), "mm/h", 1.0),
        "air_temperature_c": (("air_temperature", "ta"), "degC", 1.0),
    }

    def __init__(self, api_key: str, station_ids: set[str] | None = None) -> None:
        self.api_key = api_key
        self.station_ids = station_ids or set()
        super().__init__()

    async def fetch(self) -> AdapterResult:
        headers = {"Authorization": self.api_key}
        listing_url = f"{self.base_url}/datasets/{self.dataset}/versions/{self.version}/files"
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            listing = await client.get(
                listing_url,
                params={"maxKeys": 1, "orderBy": "created", "sorting": "desc"},
                headers=headers,
            )
            listing.raise_for_status()
            files = listing.json().get("files", [])
            if not files:
                raise RuntimeError("KNMI dataset contains no files")
            filename = files[0]["filename"]
            url_response = await client.get(f"{listing_url}/{filename}/url", headers=headers)
            url_response.raise_for_status()
            download_url = url_response.json().get("temporaryDownloadUrl")
            if not download_url:
                raise RuntimeError("KNMI temporary download URL missing")
            data_response = await client.get(download_url)
            data_response.raise_for_status()
        rows = self.parse(data_response.content, source_url=listing_url)
        now = datetime.now(UTC)
        return AdapterResult(
            stream_id=self.stream_id,
            provider=self.provider,
            source_url=listing_url,
            raw=data_response.content,
            observed_at=max((row.observed_at for row in rows), default=now),
            measurements=rows,
            metadata={"filename": filename, "station_labels": "official"},
        )

    @staticmethod
    def _scalar(
        dataset: xr.Dataset,
        names: tuple[str, ...],
        station_dimension: str,
        station_index: int,
    ) -> Any:
        for name in names:
            if name not in dataset:
                continue
            data = dataset[name]
            if station_dimension in data.dims:
                data = data.isel({station_dimension: station_index})
            values = np.asarray(data.values)
            if values.ndim == 0:
                return values.item()
            if values.size:
                return values.reshape(-1)[-1]
        return None

    @staticmethod
    def _station_id(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode(errors="replace").strip()
        if isinstance(value, np.ndarray):
            flattened = value.reshape(-1)
            if flattened.dtype.kind in {"S", "U"}:
                parts = [
                    item.decode(errors="replace") if isinstance(item, bytes) else str(item)
                    for item in flattened
                ]
                return "".join(parts).strip()
        return str(value).strip()

    @classmethod
    def _station_name(
        cls,
        dataset: xr.Dataset,
        station_dimension: str,
        station_index: int,
    ) -> str | None:
        for candidate in ("stationname", "station_name", "name"):
            if candidate not in dataset:
                continue
            data = dataset[candidate]
            if station_dimension in data.dims:
                data = data.isel({station_dimension: station_index})
            name = cls._station_id(np.asarray(data.values))
            if name:
                return name
        return None

    def _station_allowed(self, station: str) -> bool:
        if not self.station_ids:
            return True
        normalized = station.casefold()
        return any(
            normalized == candidate.casefold() or normalized.endswith(candidate.casefold().zfill(3))
            for candidate in self.station_ids
        )

    def parse(self, raw: bytes, *, source_url: str) -> list[MeasurementRecord]:
        now = datetime.now(UTC)
        with xr.open_dataset(io.BytesIO(raw), engine="h5netcdf") as dataset:
            station_values = None
            station_dimension = "station"
            for candidate in ("station", "station_id", "station_name"):
                if candidate in dataset.coords or candidate in dataset:
                    station_data = dataset[candidate]
                    station_dimension = (
                        str(station_data.dims[0]) if station_data.dims else "station"
                    )
                    station_values = np.asarray(station_data.values)
                    break
            if station_values is None:
                station_count = dataset.sizes.get(station_dimension, 1)
                station_values = np.asarray([str(index) for index in range(station_count)])
            if station_values.ndim > 1 and station_values.shape[0] == dataset.sizes.get(
                station_dimension, station_values.shape[0]
            ):
                station_rows = [station_values[index] for index in range(station_values.shape[0])]
            else:
                station_rows = list(station_values.reshape(-1))
            time_values = None
            for candidate in ("time", "timestamp", "datetime"):
                if candidate in dataset.coords or candidate in dataset:
                    time_values = np.asarray(dataset[candidate].values).reshape(-1)
                    break
            observed = now
            if time_values is not None and time_values.size:
                parsed = np.datetime64(time_values[-1], "ms").astype(datetime)
                observed = (
                    parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
                )
            rows: list[MeasurementRecord] = []
            for station_index, station_raw in enumerate(station_rows):
                station = self._station_id(station_raw)
                if not self._station_allowed(station):
                    continue
                station_name = self._station_name(dataset, station_dimension, station_index)
                latitude = self._scalar(
                    dataset, ("latitude", "lat"), station_dimension, station_index
                )
                longitude = self._scalar(
                    dataset, ("longitude", "lon"), station_dimension, station_index
                )
                location = None
                if latitude is not None and longitude is not None:
                    location = LocationRef(
                        location_id=f"knmi:{station}",
                        label=(
                            f"{station_name} ({station})"
                            if station_name and station_name.casefold() != station.casefold()
                            else f"KNMI meetstation {station}"
                        ),
                        latitude=float(latitude),
                        longitude=float(longitude),
                        geometry_source="source",
                    )
                for metric, (variables, unit, multiplier) in self._metrics.items():
                    selected_data = None
                    value = None
                    for variable in variables:
                        if variable not in dataset:
                            continue
                        data = dataset[variable]
                        try:
                            selected = data
                            if station_dimension in selected.dims:
                                selected = selected.isel({station_dimension: station_index})
                            time_dimension = next(
                                (
                                    name
                                    for name in ("time", "timestamp", "datetime")
                                    if name in selected.dims
                                ),
                                None,
                            )
                            if time_dimension:
                                selected = selected.isel({time_dimension: -1})
                            candidate = float(np.asarray(selected.values).reshape(-1)[-1])
                        except (IndexError, TypeError, ValueError):
                            continue
                        if not np.isnan(candidate):
                            value = candidate
                            selected_data = data
                            break
                    if value is None or selected_data is None:
                        continue
                    record_id = (
                        f"{self.stream_id}:"
                        f"{hashlib.sha256(f'{station}:{metric}:{observed.isoformat()}'.encode()).hexdigest()[:20]}"
                    )
                    source_ref = SourceRef(
                        stream_id=self.stream_id,
                        record_id=record_id,
                        source_url=source_url,
                        owner=self.owner,
                        trust_tier=TrustTier.OFFICIAL_MEASUREMENT,
                        observed_at=observed,
                        ingested_at=now,
                    )
                    rows.append(
                        MeasurementRecord(
                            record_id=record_id,
                            stream_id=self.stream_id,
                            metric=metric,
                            value=value * multiplier,
                            unit=unit,
                            observed_at=observed,
                            ingested_at=now,
                            location=location,
                            source_ref=source_ref,
                            quality_flags=tuple(
                                ["knmi_quality_flag"]
                                if selected_data.attrs.get("quality") not in {None, "good"}
                                else []
                            ),
                        )
                    )
        return rows
