from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from statistics import mean, median, pstdev
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from talk2dashboard.deterministic import haversine_m, pearson
from talk2dashboard.domain import DataHandle, HandleKind
from talk2dashboard.errors import InsufficientBaselineError, InsufficientSeriesError
from talk2dashboard.storage.database import Database
from talk2dashboard.storage.models import DataHandleRow, NormalizedRecordRow, SourceBundleRow

EVENT_STREAM_IDS = frozenset({"ndw_incidents", "p2000", "ns_disruptions", "nos_rss"})
MEASUREMENT_STREAM_IDS = frozenset({"knmi_observations", "rws_water", "luchtmeetnet"})
MAX_CROSS_SOURCE_RADIUS_M = 10_000


def canonical_hash(value: Any, prefix: str = "qry") -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def parse_window(value: str | None) -> timedelta | None:
    if not value:
        return None
    if value.startswith("PT") and value.endswith("M"):
        return timedelta(minutes=int(value[2:-1]))
    if value.startswith("PT") and value.endswith("H"):
        return timedelta(hours=int(value[2:-1]))
    if value.startswith("P") and value.endswith("D"):
        return timedelta(days=int(value[1:-1]))
    raise ValueError("window must use PT#M, PT#H or P#D")


class QueryEngine:
    def __init__(self, database: Database) -> None:
        self.database = database

    def latest_bundle(self) -> str:
        with self.database.session() as session:
            row = session.scalars(
                select(SourceBundleRow).order_by(SourceBundleRow.created_at.desc())
            ).first()
            if row is None:
                raise RuntimeError("No source bundle available")
            return row.bundle_version

    def _rows_for_bundle(self, bundle_version: str) -> list[dict[str, Any]]:
        with self.database.session() as session:
            bundle = session.get(SourceBundleRow, bundle_version)
            if bundle is None:
                raise KeyError(f"Unknown source bundle: {bundle_version}")
            snapshot_ids = json.loads(bundle.snapshot_ids_json)
            records = session.scalars(
                select(NormalizedRecordRow).where(NormalizedRecordRow.snapshot_id.in_(snapshot_ids))
            ).all()
            return [json.loads(record.payload_json) for record in records]

    def _rows_for_history(self) -> list[dict[str, Any]]:
        with self.database.session() as session:
            records = session.scalars(
                select(NormalizedRecordRow).order_by(NormalizedRecordRow.observed_at.asc())
            ).all()
        latest_by_record: dict[str, dict[str, Any]] = {}
        for record in records:
            latest_by_record[record.record_id] = json.loads(record.payload_json)
        return list(latest_by_record.values())

    def prepare(
        self, query_spec: dict[str, Any], bundle_version: str | None = None
    ) -> tuple[HandleKind, dict[str, Any], list[dict[str, Any]], str]:
        """Run the read-only query phase without persisting a handle.

        Independent batch queries may prepare concurrently. Handle persistence stays
        serial because SQLite only has one writer and concurrent inserts add lock
        latency without improving query throughput.
        """
        normalized = self._normalize_query(query_spec)
        input_handle_id = normalized.get("input_handle")
        if input_handle_id:
            input_handle, source_rows = self.load(str(input_handle_id))
            bundle_version = bundle_version or input_handle.source_bundle_version
        else:
            bundle_version = bundle_version or self.latest_bundle()
            source_rows = (
                self._rows_for_history()
                if normalized.get("operation") == "baseline"
                else self._rows_for_bundle(bundle_version)
            )
        rows = self._filter(source_rows, normalized)
        operation = normalized.get("operation", "query")
        kind: HandleKind = "events" if normalized.get("record_kind") == "event" else "series"
        if operation == "aggregate":
            rows, kind = self._aggregate(rows, normalized), "aggregate"
        elif operation == "baseline":
            rows, kind = self._baseline(rows, normalized), "baseline"
        elif operation == "correlate":
            rows, kind = self._correlate(rows, normalized), "correlation"
        return kind, normalized, rows, bundle_version

    def execute(self, query_spec: dict[str, Any], bundle_version: str | None = None) -> DataHandle:
        kind, normalized, rows, resolved_bundle = self.prepare(query_spec, bundle_version)
        return self.create_handle(kind, normalized, rows, resolved_bundle)

    def create_handle(
        self,
        kind: HandleKind,
        query_spec: dict[str, Any],
        rows: list[dict[str, Any]],
        bundle_version: str | None = None,
        ttl_seconds: int | None = None,
    ) -> DataHandle:
        bundle_version = bundle_version or self.latest_bundle()
        query_hash = canonical_hash(query_spec)
        fields = tuple(sorted({key for row in rows for key in row}))
        schema_fingerprint = canonical_hash(fields, "schema")
        handle_id = canonical_hash(
            {"query_hash": query_hash, "bundle": bundle_version, "schema": schema_fingerprint},
            "hdl",
        )
        observed = [
            self._as_datetime(row.get("observed_at")) for row in rows if row.get("observed_at")
        ]
        handle = DataHandle(
            handle_id=handle_id,
            kind=kind,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds) if ttl_seconds else None,
            source_bundle_version=bundle_version,
            schema_fingerprint=schema_fingerprint,
            fields=fields,
            row_count=len(rows),
            preview=tuple(rows[:5]),
            summary=self._summary(kind, rows, query_spec),
            source_refs=tuple(sorted(self._source_refs(rows))),
            query_hash=query_hash,
            freshness={
                "oldest": min(observed) if observed else None,
                "newest": max(observed) if observed else None,
            },
        )
        # Rendering the same logical binding is common after a page refresh. Reuse
        # the content-addressed handle instead of competing with source ingestion
        # for a second, identical SQLite write transaction.
        with self.database.session() as session:
            stored = session.get(DataHandleRow, handle.handle_id)
            if stored is not None:
                return DataHandle.model_validate(json.loads(stored.payload_json)["handle"])

        payload_json = json.dumps(
            {
                "handle": handle.model_dump(mode="json"),
                "rows": rows,
                "query_spec": query_spec,
            },
            ensure_ascii=True,
        )
        with self.database.session() as session:
            session.execute(
                sqlite_insert(DataHandleRow)
                .values(
                    handle_id=handle.handle_id,
                    kind=handle.kind,
                    source_bundle_version=bundle_version,
                    query_hash=query_hash,
                    schema_fingerprint=schema_fingerprint,
                    created_at=handle.created_at.isoformat(),
                    expires_at=handle.expires_at.isoformat() if handle.expires_at else None,
                    payload_json=payload_json,
                )
                .on_conflict_do_nothing(index_elements=[DataHandleRow.handle_id])
            )
            stored = session.get(DataHandleRow, handle.handle_id)
            assert stored is not None
            return DataHandle.model_validate(json.loads(stored.payload_json)["handle"])

    def load(self, handle_id: str) -> tuple[DataHandle, list[dict[str, Any]]]:
        payload = self.load_payload(handle_id)
        return DataHandle.model_validate(payload["handle"]), payload["rows"]

    def load_payload(self, handle_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            row = session.get(DataHandleRow, handle_id)
            if row is None:
                raise KeyError(f"Unknown handle: {handle_id}")
            if row.expires_at and self._as_datetime(row.expires_at) <= datetime.now(UTC):
                raise KeyError(f"Expired handle: {handle_id}")
            return json.loads(row.payload_json)

    def latest_compatible(
        self,
        query_hash: str,
        schema_fingerprint: str,
        *,
        exclude_bundle: str | None = None,
    ) -> DataHandle | None:
        with self.database.session() as session:
            statement = (
                select(DataHandleRow)
                .where(
                    DataHandleRow.query_hash == query_hash,
                    DataHandleRow.schema_fingerprint == schema_fingerprint,
                )
                .order_by(DataHandleRow.created_at.desc())
            )
            if exclude_bundle:
                statement = statement.where(DataHandleRow.source_bundle_version != exclude_bundle)
            row = session.scalars(statement).first()
            if row is None:
                return None
            return DataHandle.model_validate(json.loads(row.payload_json)["handle"])

    def _normalize_query(self, query: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "operation",
            "record_kind",
            "stream",
            "streams",
            "metric",
            "metrics",
            "window",
            "since",
            "until",
            "category",
            "categories",
            "severity",
            "status",
            "text",
            "limit",
            "group_by",
            "fn",
            "series_a",
            "series_b",
            "input_handle",
            "filters",
            "sort",
            "order",
            "lag",
            "method",
            "origin_handle",
            "origin_record_id",
            "radius_m",
        }
        unknown = set(query) - allowed
        if unknown:
            raise ValueError(f"Unsupported query fields: {', '.join(sorted(unknown))}")
        normalized = {key: value for key, value in query.items() if value not in (None, "", [])}
        normalized.setdefault("operation", "query")
        aliases = {
            "query_events": ("query", "event"),
            "query_measurements": ("query", "measurement"),
            "query_series": ("query", "measurement"),
        }
        if normalized["operation"] in aliases:
            operation, record_kind = aliases[normalized["operation"]]
            normalized["operation"] = operation
            normalized.setdefault("record_kind", record_kind)
        if normalized["operation"] == "query_nearby":
            stream = normalized.get("stream")
            if not stream or normalized.get("streams"):
                raise ValueError("query_nearby requires exactly one target stream")
            if stream in EVENT_STREAM_IDS:
                normalized.setdefault("record_kind", "event")
            elif stream in MEASUREMENT_STREAM_IDS:
                normalized.setdefault("record_kind", "measurement")
            else:
                raise ValueError(f"query_nearby does not support target stream: {stream}")
            origin_handle = normalized.get("origin_handle")
            if not origin_handle:
                raise ValueError("query_nearby requires origin_handle")
            radius_m = int(normalized.get("radius_m", MAX_CROSS_SOURCE_RADIUS_M))
            if not 1 <= radius_m <= MAX_CROSS_SOURCE_RADIUS_M:
                raise ValueError("radius_m must be between 1 and 10000")
            spatial_filter: dict[str, Any] = {
                "op": "within_radius_handle",
                "field": "location",
                "handle_id": str(origin_handle),
                "radius_m": radius_m,
            }
            if normalized.get("origin_record_id"):
                spatial_filter["origin_record_id"] = str(normalized["origin_record_id"])
            normalized["filters"] = [*(normalized.get("filters") or []), spatial_filter]
            normalized.setdefault("sort", "distance_m")
            normalized.setdefault("order", "asc")
        normalized.setdefault("limit", 500)
        normalized["limit"] = max(1, min(int(normalized["limit"]), 2000))
        parse_window(normalized.get("window"))
        return normalized

    def _filter(self, rows: list[dict[str, Any]], query: dict[str, Any]) -> list[dict[str, Any]]:
        streams = set(query.get("streams") or ([query["stream"]] if query.get("stream") else []))
        metrics = set(query.get("metrics") or ([query["metric"]] if query.get("metric") else []))
        categories = set(
            query.get("categories") or ([query["category"]] if query.get("category") else [])
        )
        window = parse_window(query.get("window"))
        filters = query.get("filters") or []
        spatial_filters = self._prepare_spatial_filters(filters)
        scalar_filters = [item for item in filters if item.get("op") != "within_radius_handle"]
        result: list[dict[str, Any]] = []
        for row in rows:
            if streams and row.get("stream_id") not in streams:
                continue
            is_event = "category" in row
            if query.get("record_kind") == "event" and not is_event:
                continue
            if query.get("record_kind") == "measurement" and is_event:
                continue
            if metrics and row.get("metric") not in metrics:
                continue
            if categories and row.get("category") not in categories:
                continue
            if query.get("severity") and row.get("severity") != query["severity"]:
                continue
            if query.get("status") and row.get("status") != query["status"]:
                continue
            text = str(query.get("text", "")).casefold()
            if text and text not in json.dumps(row, ensure_ascii=False).casefold():
                continue
            if not self._matches_filters(row, scalar_filters):
                continue
            candidate = dict(row)
            if not self._apply_spatial_filters(candidate, spatial_filters):
                continue
            result.append(candidate)
        if window:
            observed_values = [
                self._as_datetime(row.get("observed_at"))
                for row in result
                if row.get("observed_at")
            ]
            anchor = max(observed_values) if observed_values else datetime.now(UTC)
            cutoff = anchor - window
            result = [row for row in result if self._as_datetime(row.get("observed_at")) >= cutoff]
        sort_field = str(query.get("sort") or "observed_at")
        result.sort(key=lambda item: self._sort_value(self._nested(item, sort_field)))
        descending = query.get("order") == "desc"
        if descending:
            result.reverse()
        if descending or sort_field == "distance_m":
            return result[: query["limit"]]
        return result[-query["limit"] :]

    @staticmethod
    def _sort_value(value: Any) -> tuple[int, float | str]:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (0, float(value))
        if value is None:
            return (2, "")
        return (1, str(value).casefold())

    @staticmethod
    def _nested(row: dict[str, Any], field: str) -> Any:
        value: Any = row
        for part in field.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    def _matches_filters(self, row: dict[str, Any], filters: list[dict[str, Any]]) -> bool:
        allowed = {"eq", "in", "gte", "lte", "between", "contains"}
        for item in filters:
            operation = item.get("op")
            if operation not in allowed:
                raise ValueError(f"Unsupported filter operation: {operation}")
            actual = self._nested(row, str(item.get("field", "")))
            expected = item.get("value")
            if operation == "eq" and actual != expected:
                return False
            if operation == "in" and actual not in (expected or []):
                return False
            if operation == "gte" and (actual is None or actual < expected):
                return False
            if operation == "lte" and (actual is None or actual > expected):
                return False
            if operation == "between":
                if not isinstance(expected, list | tuple) or len(expected) != 2:
                    raise ValueError("between filter requires [low, high]")
                low, high = expected
                if actual is None or not low <= actual <= high:
                    return False
            if operation == "contains" and str(expected).casefold() not in str(actual).casefold():
                return False
        return True

    def _prepare_spatial_filters(
        self, filters: list[dict[str, Any]]
    ) -> list[tuple[int, list[dict[str, Any]]]]:
        prepared: list[tuple[int, list[dict[str, Any]]]] = []
        for item in filters:
            if item.get("op") != "within_radius_handle":
                continue
            handle_id = str(item.get("handle_id") or "")
            if not handle_id:
                raise ValueError("within_radius_handle requires handle_id")
            radius_m = int(item.get("radius_m", 5000))
            if not 1 <= radius_m <= MAX_CROSS_SOURCE_RADIUS_M:
                raise ValueError("radius_m must be between 1 and 10000")
            _handle, origin_rows = self.load(handle_id)
            origin_record_id = item.get("origin_record_id")
            if origin_record_id:
                origin_rows = [
                    row for row in origin_rows if str(row.get("record_id")) == str(origin_record_id)
                ]
                if not origin_rows:
                    raise ValueError(f"origin_record_id not found in handle: {origin_record_id}")
            located_origins = [row for row in origin_rows if row.get("location")]
            if not located_origins:
                raise ValueError("origin handle contains no records with coordinates")
            prepared.append((radius_m, located_origins))
        return prepared

    def _apply_spatial_filters(
        self,
        row: dict[str, Any],
        spatial_filters: list[tuple[int, list[dict[str, Any]]]],
    ) -> bool:
        if not spatial_filters:
            return True
        location = row.get("location")
        if not location:
            return False
        for radius_m, origins in spatial_filters:
            nearest = min(
                (
                    (self._distance_m(location, origin["location"]), origin)
                    for origin in origins
                ),
                key=lambda item: item[0],
            )
            distance_m, origin = nearest
            if distance_m > radius_m:
                return False
            origin_location = origin.get("location") or {}
            row["distance_m"] = round(distance_m, 1)
            row["distance_origin_record_id"] = str(origin.get("record_id") or "")
            row["distance_origin_label"] = str(
                origin_location.get("label") or origin.get("title") or origin.get("record_id") or ""
            )
        return True

    @staticmethod
    def _distance_m(a: dict[str, Any], b: dict[str, Any]) -> float:
        return haversine_m(
            float(a["latitude"]),
            float(a["longitude"]),
            float(b["latitude"]),
            float(b["longitude"]),
        )

    def _aggregate(self, rows: list[dict[str, Any]], query: dict[str, Any]) -> list[dict[str, Any]]:
        fn = query.get("fn", "count")
        group_by = query.get("group_by")
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            key = str(self._nested(row, group_by) or "onbekend") if group_by else "all"
            groups.setdefault(key, []).append(row)
        output = []
        for key, items in groups.items():
            values = [
                float(item["value"])
                for item in items
                if isinstance(item.get("value"), (int, float))
            ]
            if fn == "count":
                value = len(items)
            elif fn == "sum":
                value = sum(values)
            elif fn == "mean":
                value = mean(values) if values else None
            elif fn == "median":
                value = median(values) if values else None
            elif fn == "min":
                value = min(values) if values else None
            elif fn == "max":
                value = max(values) if values else None
            elif fn == "p95":
                value = (
                    sorted(values)[max(0, math.ceil(len(values) * 0.95) - 1)] if values else None
                )
            elif fn == "latest":
                value = values[-1] if values else None
            elif fn == "delta":
                value = values[-1] - values[0] if len(values) >= 2 else None
            elif fn == "percent_change":
                value = (
                    ((values[-1] - values[0]) / abs(values[0])) * 100
                    if len(values) >= 2 and values[0] != 0
                    else None
                )
            else:
                raise ValueError(f"Unsupported aggregate fn: {fn}")
            metrics = {str(item["metric"]) for item in items if item.get("metric")}
            units = {str(item["unit"]) for item in items if item.get("unit")}
            output.append(
                {
                    "group": key,
                    "value": value,
                    "fn": fn,
                    "count": len(items),
                    "metric": next(iter(metrics)) if len(metrics) == 1 else None,
                    "unit": next(iter(units)) if len(units) == 1 else None,
                }
            )
        return output

    def _baseline(self, rows: list[dict[str, Any]], _query: dict[str, Any]) -> list[dict[str, Any]]:
        values = [float(row["value"]) for row in rows if isinstance(row.get("value"), (int, float))]
        if len(values) < 3:
            raise InsufficientBaselineError(0.0)
        fixture_only = all(
            (row.get("source_ref") or {}).get("trust_tier") == "fixture" for row in rows
        )
        observed = sorted(
            self._as_datetime(row["observed_at"]) for row in rows if row.get("observed_at")
        )
        available_days = (observed[-1] - observed[0]).total_seconds() / 86400 if observed else 0
        if not fixture_only and available_days < 14:
            raise InsufficientBaselineError(available_days)
        current = values[-1]
        baseline = mean(values[:-1]) if len(values) > 1 else current
        deviation = pstdev(values[:-1]) if len(values) > 2 else 0.0
        z_score = (current - baseline) / deviation if deviation else 0.0
        return [
            {
                "current": current,
                "baseline": baseline,
                "delta": current - baseline,
                "z_score": z_score,
                "sample_size": len(values) - 1,
                "baseline_source": "fixture_baseline" if fixture_only else "historical_store",
                "available_days": round(available_days, 2),
            }
        ]

    def _correlate(self, rows: list[dict[str, Any]], query: dict[str, Any]) -> list[dict[str, Any]]:
        a, b = query.get("series_a"), query.get("series_b")
        values_a = [float(row["value"]) for row in rows if row.get("metric") == a]
        values_b = [float(row["value"]) for row in rows if row.get("metric") == b]
        count = min(len(values_a), len(values_b))
        if count < 3:
            raise InsufficientSeriesError(count)
        return [
            {
                "series_a": a,
                "series_b": b,
                "correlation": pearson(values_a, values_b),
                "sample_size": count,
                "causality": False,
            }
        ]

    @staticmethod
    def _as_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    @staticmethod
    def _source_refs(rows: list[dict[str, Any]]) -> set[str]:
        refs = set()
        for row in rows:
            source = row.get("source_ref") or {}
            if source.get("stream_id") and source.get("record_id"):
                refs.add(f"{source['stream_id']}:{source['record_id']}")
        return refs

    @staticmethod
    def _summary(kind: HandleKind, rows: list[dict[str, Any]], query: dict[str, Any]) -> str:
        if not rows:
            return "Geen records binnen het gevraagde filter."
        if kind == "aggregate":
            return f"{len(rows)} deterministische aggregatierijen."
        if kind == "baseline":
            return "Actuele waarde vergeleken met beschikbare baseline."
        if kind == "correlation":
            return "Deterministische correlatie; dit bewijst geen causaliteit."
        return f"{len(rows)} records voor {query.get('stream') or 'de geselecteerde streams'}."
