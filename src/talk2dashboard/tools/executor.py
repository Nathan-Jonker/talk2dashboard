from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from talk2dashboard.agent_catalog import NEARBY_PLACE_TYPES
from talk2dashboard.dashboard import DashboardService
from talk2dashboard.deterministic import haversine_m, pearson
from talk2dashboard.domain import (
    MAX_VISIBLE_PANELS as DASHBOARD_PANEL_LIMIT,
)
from talk2dashboard.domain import (
    MULTI_BINDING_PANEL_TYPES,
    DashboardOperation,
    LogicalDataBinding,
    PanelSpec,
    ToolRequest,
    ToolResponse,
)
from talk2dashboard.errors import ContractError, InsufficientSeriesError
from talk2dashboard.integrations.places import GeocodingClient, PlacesClient
from talk2dashboard.integrations.search import BraveSearchClient
from talk2dashboard.locations import EphemeralLocationStore
from talk2dashboard.panel_contracts import compatibility_summary, evaluate_panel
from talk2dashboard.query import QueryEngine
from talk2dashboard.sources.service import SourceService
from talk2dashboard.storage.database import Database
from talk2dashboard.storage.models import IncidentClusterRow, SessionPolicyRow, ToolAuditRow

CaptureCallable = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class ToolExecutionError(ContractError):
    pass


class ToolExecutor:
    HIDDEN_PANEL_TYPES: ClassVar[set[str]] = {"source_health", "evidence"}
    MAX_VISIBLE_PANELS: ClassVar[int] = DASHBOARD_PANEL_LIMIT
    PARALLEL_QUERY_OPERATIONS: ClassVar[set[str]] = {
        "query",
        "query_events",
        "query_measurements",
        "query_series",
        "query_nearby",
        "aggregate",
        "baseline",
    }

    def __init__(
        self,
        database: Database,
        sources: SourceService,
        query: QueryEngine,
        dashboard: DashboardService,
        places: PlacesClient,
        geocoding: GeocodingClient,
        search: BraveSearchClient,
        capture: CaptureCallable | None = None,
    ) -> None:
        self.database = database
        self.sources = sources
        self.query = query
        self.dashboard = dashboard
        self.places = places
        self.geocoding = geocoding
        self.search = search
        self.capture = capture
        self.locations = EphemeralLocationStore(database)

    async def execute(self, name: str, request: ToolRequest) -> ToolResponse:
        with self.database.session() as session:
            existing = session.scalars(
                select(ToolAuditRow).where(ToolAuditRow.request_id == request.request_id)
            ).first()
            if existing:
                return ToolResponse(
                    ok=existing.ok,
                    result=json.loads(existing.result_json) if existing.result_json else None,
                    error=(
                        json.loads(existing.error_json)
                        if not existing.ok and existing.error_json
                        else {"code": existing.error_code, "message": "Idempotent replay"}
                        if not existing.ok
                        else None
                    ),
                    duration_ms=existing.duration_ms,
                    source_bundle_version=existing.source_bundle_version,
                    dashboard_version=existing.dashboard_after,
                    audit_ref=existing.audit_id,
                )
        started = time.monotonic_ns()
        if name == "dashboard_batch":
            with self.database.session() as session:
                before = self.dashboard.current(optional=True, session=session)
                result, error, ok = await self._invoke(name, request, session=session)
                ended = time.monotonic_ns()
                after = self.dashboard.current(optional=True, session=session)
                bundle = self.sources.latest_bundle_version()
                audit_ref = f"audit_{uuid4().hex}"
                session.add(
                    self._audit_row(
                        audit_ref,
                        name,
                        request,
                        result,
                        error,
                        ok,
                        started,
                        ended,
                        before.version if before else None,
                        after.version if after else None,
                        bundle,
                    )
                )
            return self._response(result, error, ok, started, ended, bundle, after, audit_ref)

        before = self.dashboard.current(optional=True)
        audit_ref = f"audit_{uuid4().hex}"
        result, error, ok = await self._invoke(name, request)
        ended = time.monotonic_ns()
        after = self.dashboard.current(optional=True)
        bundle = self.sources.latest_bundle_version()
        with self.database.session() as session:
            session.add(
                self._audit_row(
                    audit_ref,
                    name,
                    request,
                    result,
                    error,
                    ok,
                    started,
                    ended,
                    before.version if before else None,
                    after.version if after else None,
                    bundle,
                )
            )
        return self._response(result, error, ok, started, ended, bundle, after, audit_ref)

    async def _invoke(
        self, name: str, request: ToolRequest, *, session: Session | None = None
    ) -> tuple[dict[str, Any] | list[Any] | None, dict[str, Any] | None, bool]:
        result: dict[str, Any] | list[Any] | None = None
        error: dict[str, Any] | None = None
        ok = True
        try:
            current_policy = self.policy()
            if request.session_policy_version != current_policy["version"]:
                raise ToolExecutionError(
                    "POLICY_VERSION_CONFLICT",
                    f"Beleid is gewijzigd; actuele versie is {current_policy['version']}.",
                )
            handler = getattr(self, f"_tool_{name.replace('-', '_')}", None)
            if handler is None:
                raise ToolExecutionError("UNKNOWN_TOOL", f"Unknown tool: {name}")
            payload = dict(request.payload)
            if name == "dashboard_batch" and "expected_version" not in payload:
                payload["expected_version"] = request.dashboard_version
            result = (
                await self._tool_dashboard_batch(payload, session=session)
                if name == "dashboard_batch"
                else await handler(payload)
            )
        except ContractError as exc:
            ok, error = False, exc.as_dict()
        except (KeyError, ValueError) as exc:
            ok, error = False, {"code": "INVALID_ARGUMENT", "message": str(exc)[:400]}
        except Exception as exc:
            ok, error = False, {"code": type(exc).__name__.upper(), "message": str(exc)[:400]}
        return result, error, ok

    @staticmethod
    def _audit_row(
        audit_ref: str,
        name: str,
        request: ToolRequest,
        result: dict[str, Any] | list[Any] | None,
        error: dict[str, Any] | None,
        ok: bool,
        started: int,
        ended: int,
        dashboard_before: int | None,
        dashboard_after: int | None,
        bundle: str | None,
    ) -> ToolAuditRow:
        return ToolAuditRow(
            audit_id=audit_ref,
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
            request_id=request.request_id,
            tool_name=name,
            arguments_json=json.dumps(request.payload, ensure_ascii=True),
            result_json=json.dumps(result, ensure_ascii=True, default=str)
            if result is not None
            else None,
            ok=ok,
            error_code=error["code"] if error else None,
            error_json=json.dumps(error, ensure_ascii=True, default=str) if error else None,
            started_ns=started,
            ended_ns=ended,
            duration_ms=(ended - started) / 1_000_000,
            dashboard_before=dashboard_before,
            dashboard_after=dashboard_after,
            source_bundle_version=bundle,
            created_at=datetime.now(UTC).isoformat(),
        )

    @staticmethod
    def _response(
        result: dict[str, Any] | list[Any] | None,
        error: dict[str, Any] | None,
        ok: bool,
        started: int,
        ended: int,
        bundle: str | None,
        after: Any,
        audit_ref: str,
    ) -> ToolResponse:
        return ToolResponse(
            ok=ok,
            result=result,
            error=error,
            duration_ms=(ended - started) / 1_000_000,
            source_bundle_version=bundle,
            dashboard_version=after.version if after else None,
            audit_ref=audit_ref,
        )

    async def _tool_inspect_workspace(self, payload: dict[str, Any]) -> dict[str, Any]:
        detail = str(payload.get("detail", "compact"))
        ids = [str(value) for value in payload.get("ids") or []]
        if detail not in {"ids", "compact", "full"}:
            raise ToolExecutionError("INVALID_ARGUMENT", "detail must be ids, compact or full")
        if detail == "full" and not 1 <= len(ids) <= 5:
            raise ToolExecutionError(
                "FULL_DETAIL_REQUIRES_IDS", "full detail requires one to five explicit IDs"
            )
        sections = set(payload.get("sections") or [])
        aliases = {
            "stream_schema": "schemas",
            "panel_types": "panels",
            "policies": "policy",
        }
        sections = {aliases.get(item, item) for item in sections}
        output: dict[str, Any] = {}
        if "streams" in sections or "schemas" in sections:
            streams = [item.model_dump(mode="json") for item in self.sources.health()]
            if ids:
                streams = [item for item in streams if item["stream_id"] in ids]
            output["streams"] = (
                [item["stream_id"] for item in streams] if detail == "ids" else streams
            )
        if "schemas" in sections:
            output["stream_contract"] = {
                "events": [
                    "record_id",
                    "stream_id",
                    "category",
                    "title",
                    "severity",
                    "status",
                    "observed_at",
                    "location",
                    "attributes",
                    "source_ref",
                    "quality_flags",
                ],
                "measurements": [
                    "record_id",
                    "stream_id",
                    "metric",
                    "value",
                    "unit",
                    "observed_at",
                    "location",
                    "source_ref",
                    "quality_flags",
                ],
                "filter_ops": [
                    "eq",
                    "in",
                    "gte",
                    "lte",
                    "between",
                    "contains",
                    "within_radius_handle",
                ],
            }
        if "panels" in sections:
            output["panel_types"] = {
                "kpi": ["aggregate", "baseline"],
                "timeseries": ["series"],
                "ranking": ["series", "aggregate"],
                "comparison": ["aggregate", "baseline", "diff"],
                "incident_timeline": ["events", "incident"],
                "event_table": ["events"],
                "source_health": [],
                "evidence": ["events", "series", "incident", "web_results"],
                "map_2d": ["events", "places", "incident"],
                "map_3d_google": ["events", "places", "incident"],
                "nearby_places": ["places"],
                "correlation": ["correlation"],
                "change_summary": ["diff"],
                "ai_brief": ["events", "series", "incident"],
            }
        if "dashboard" in sections:
            spec = self.dashboard.current()
            dashboard_data = spec.model_dump(mode="json") if spec else None
            if dashboard_data and ids:
                dashboard_data["panels"] = [
                    panel for panel in dashboard_data["panels"] if panel["panel_id"] in ids
                ]
            output["dashboard"] = dashboard_data
        if "policy" in sections:
            output["policy"] = self.policy()
        if "incidents" in sections:
            with self.database.session() as session:
                rows = session.scalars(
                    select(IncidentClusterRow)
                    .order_by(IncidentClusterRow.last_seen_at.desc())
                    .limit(20)
                ).all()
                incidents = [json.loads(row.payload_json) for row in rows]
                if ids:
                    incidents = [item for item in incidents if item["incident_id"] in ids]
                output["incidents"] = (
                    [item["incident_id"] for item in incidents] if detail == "ids" else incidents
                )
        encoded = json.dumps(output, ensure_ascii=True, default=str)
        if len(encoded.encode()) > 48_000:
            raise ToolExecutionError(
                "INSPECT_PAYLOAD_TOO_LARGE",
                "Selecteer minder IDs of gebruik compact detail.",
                details={"limit_bytes": 48000, "actual_bytes": len(encoded.encode())},
            )
        return output

    @staticmethod
    def _resolve_aliases(value: Any, aliases: dict[str, str]) -> Any:
        if isinstance(value, str) and value.startswith("@"):
            return aliases[value[1:]]
        if isinstance(value, list):
            return [ToolExecutor._resolve_aliases(item, aliases) for item in value]
        if isinstance(value, dict):
            return {
                key: ToolExecutor._resolve_aliases(item, aliases) for key, item in value.items()
            }
        return value

    @staticmethod
    def _alias_references(value: Any) -> set[str]:
        if isinstance(value, str) and value.startswith("@"):
            return {value[1:]}
        if isinstance(value, list):
            return set().union(*(ToolExecutor._alias_references(item) for item in value))
        if isinstance(value, dict):
            return set().union(*(ToolExecutor._alias_references(item) for item in value.values()))
        return set()

    @staticmethod
    def _data_operation_parts(operation: dict[str, Any], index: int) -> tuple[str, dict[str, Any]]:
        nested_query = operation.get("query")
        raw_query = dict(nested_query if isinstance(nested_query, dict) else operation)
        nested_save_as = raw_query.pop("save_as", None)
        save_as = str(
            operation.get("save_as") or nested_save_as or f"result_{index + 1}"
        ).removeprefix("@")
        return save_as, raw_query

    @staticmethod
    def _normalize_data_query(raw_query: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
        query_spec = ToolExecutor._resolve_aliases(raw_query, aliases)
        normalized_filters = []
        for filter_item in query_spec.get("filters") or []:
            normalized_filter = dict(filter_item)
            if "value_number" in normalized_filter:
                normalized_filter["value"] = normalized_filter.pop("value_number")
            elif "values" in normalized_filter:
                normalized_filter["value"] = normalized_filter.pop("values")
            normalized_filters.append(normalized_filter)
        if normalized_filters:
            query_spec["filters"] = normalized_filters
        return query_spec

    async def _tool_data_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        operations = payload.get("operations") or []
        if not isinstance(operations, list) or not 1 <= len(operations) <= 12:
            raise ToolExecutionError("INVALID_BATCH", "operations must contain one to twelve items")
        operation_parts = [
            self._data_operation_parts(operation, index)
            for index, operation in enumerate(operations)
        ]
        save_names = [save_as for save_as, _raw_query in operation_parts]
        if len(set(save_names)) != len(save_names):
            raise ToolExecutionError("DUPLICATE_ALIAS", "Elke data_batch-alias moet uniek zijn.")

        bundle_version = await asyncio.to_thread(self.query.latest_bundle)
        parallel_inputs: list[tuple[int, dict[str, Any]]] = []
        for index, (_save_as, raw_query) in enumerate(operation_parts):
            operation_name = str(raw_query.get("operation", "query"))
            if (
                operation_name in self.PARALLEL_QUERY_OPERATIONS
                and not self._alias_references(raw_query)
                and not (
                    operation_name == "query_nearby"
                    and (raw_query.get("origin_text") or raw_query.get("origin_resolution_id"))
                )
            ):
                parallel_inputs.append((index, self._normalize_data_query(raw_query, {})))
        parallel_prepared = await asyncio.gather(
            *(
                asyncio.to_thread(self.query.prepare, query_spec, bundle_version)
                for _index, query_spec in parallel_inputs
            )
        )
        precomputed_queries = {
            index: prepared
            for (index, _query_spec), prepared in zip(
                parallel_inputs, parallel_prepared, strict=True
            )
        }

        aliases: dict[str, str] = {}
        results = []
        queried_streams: set[str] = set()
        for index, (save_as, raw_query) in enumerate(operation_parts):
            query_spec = self._normalize_data_query(raw_query, aliases)
            queried_streams.update(str(item) for item in query_spec.get("streams") or [])
            if query_spec.get("stream"):
                queried_streams.add(str(query_spec["stream"]))
            operation_name = query_spec.get("operation", "query")
            if operation_name == "query_nearby" and (
                query_spec.get("origin_text") or query_spec.get("origin_resolution_id")
            ):
                origin_text = str(query_spec.pop("origin_text", "") or "").strip()
                resolution_id = str(query_spec.get("origin_resolution_id") or "").strip()
                if origin_text:
                    geocoded = await self.geocoding.resolve(origin_text)
                    matches = geocoded.get("matches") or []
                    if not matches:
                        raise ToolExecutionError(
                            "LOCATION_NOT_RESOLVED",
                            "Google vond geen locatie; verduidelijk het adres of de plaats.",
                        )
                    resolution = self.locations.put(origin_text, matches[0])
                    resolution_id = resolution.resolution_id
                    query_spec["origin_resolution_id"] = resolution_id
                else:
                    try:
                        resolution = self.locations.get(resolution_id)
                    except KeyError as exc:
                        raise ToolExecutionError(
                            "LOCATION_RESOLUTION_EXPIRED",
                            "De tijdelijke locatie is verlopen; resolveer de plaats opnieuw.",
                            retryable=True,
                        ) from exc
                kind, normalized_query, rows, resolved_bundle = await asyncio.to_thread(
                    self.query.prepare,
                    query_spec,
                    bundle_version,
                    origin_location={
                        "latitude": resolution.latitude,
                        "longitude": resolution.longitude,
                        "label": resolution.display_label,
                    },
                )
                handle = await asyncio.to_thread(
                    self.query.create_handle,
                    kind,
                    normalized_query,
                    rows,
                    resolved_bundle,
                    900,
                )
            elif index in precomputed_queries:
                kind, normalized_query, rows, resolved_bundle = precomputed_queries[index]
                handle = await asyncio.to_thread(
                    self.query.create_handle,
                    kind,
                    normalized_query,
                    rows,
                    resolved_bundle,
                )
            elif operation_name == "get_incident":
                with self.database.session() as session:
                    row = session.scalars(
                        select(IncidentClusterRow)
                        .where(IncidentClusterRow.incident_id == query_spec["incident_id"])
                        .order_by(IncidentClusterRow.last_seen_at.desc())
                    ).first()
                if row is None:
                    raise ToolExecutionError("INCIDENT_NOT_FOUND", "Incident bestaat niet")
                handle = self.query.create_handle(
                    "incident", query_spec, [json.loads(row.payload_json)], row.bundle_version
                )
            elif operation_name == "diff":
                _left, left_rows = self.query.load(str(query_spec["left_handle"]))
                right, right_rows = self.query.load(str(query_spec["right_handle"]))
                left_ids = {str(row.get("record_id")) for row in left_rows}
                right_ids = {str(row.get("record_id")) for row in right_rows}
                diff_rows = [
                    {
                        "added": sorted(right_ids - left_ids),
                        "removed": sorted(left_ids - right_ids),
                        "unchanged_count": len(left_ids & right_ids),
                    }
                ]
                handle = self.query.create_handle(
                    "diff", query_spec, diff_rows, right.source_bundle_version
                )
            elif operation_name == "answer_slice":
                source_handle, source_rows = self.query.load(str(query_spec["input_handle"]))
                limit = max(1, min(int(query_spec.get("limit", 20)), 100))
                handle = self.query.create_handle(
                    source_handle.kind,
                    query_spec,
                    source_rows[:limit],
                    source_handle.source_bundle_version,
                )
            elif operation_name == "resolve_location":
                text = str(query_spec["text"])
                source_handle = await asyncio.to_thread(
                    self.query.execute,
                    {"operation": "query_events", "text": text, "limit": 20},
                    bundle_version,
                )
                _, source_rows = self.query.load(source_handle.handle_id)
                located = [row for row in source_rows if row.get("location")]
                if located:
                    handle = self.query.create_handle(
                        "events", query_spec, located[:5], source_handle.source_bundle_version
                    )
                elif query_spec.get("resolver") == "google":
                    geocoded = await self.geocoding.resolve(text)
                    matches = geocoded.get("matches") or []
                    if not matches:
                        raise ToolExecutionError(
                            "LOCATION_NOT_RESOLVED",
                            "Google vond geen locatie; verduidelijk de plaats.",
                        )
                    resolutions = [self.locations.put(text, item) for item in matches]
                    results.append(
                        {
                            "alias": save_as,
                            "resolution_id": resolutions[0].resolution_id,
                            "kind": "ephemeral_location",
                            "row_count": len(resolutions),
                            "preview": [item.model_dump(mode="json") for item in resolutions],
                            "summary": "Tijdelijke Google-locatieresolutie; verloopt na vijftien minuten.",
                        }
                    )
                    aliases[str(save_as)] = resolutions[0].resolution_id
                    continue
                else:
                    raise ToolExecutionError(
                        "LOCATION_NOT_RESOLVED",
                        "Geen bronafgeleide locatie gevonden; verduidelijk of gebruik resolver google.",
                    )
            elif operation_name == "correlate" and str(query_spec.get("series_a", "")).startswith(
                "hdl_"
            ):
                left, left_rows = self.query.load(str(query_spec["series_a"]))
                _right, right_rows = self.query.load(str(query_spec["series_b"]))
                left_values = [
                    float(row["value"]) for row in left_rows if row.get("value") is not None
                ]
                right_values = [
                    float(row["value"]) for row in right_rows if row.get("value") is not None
                ]
                count = min(len(left_values), len(right_values))
                if count < 3:
                    raise InsufficientSeriesError(count)
                handle = self.query.create_handle(
                    "correlation",
                    query_spec,
                    [
                        {
                            "series_a": left.handle_id,
                            "series_b": query_spec["series_b"],
                            "correlation": pearson(left_values, right_values),
                            "sample_size": count,
                            "causality": False,
                        }
                    ],
                    left.source_bundle_version,
                )
            else:
                handle = await asyncio.to_thread(self.query.execute, query_spec, bundle_version)
            aliases[str(save_as)] = handle.handle_id
            result_item: dict[str, Any] = {
                "alias": save_as,
                "handle_id": handle.handle_id,
                "kind": handle.kind,
                "row_count": handle.row_count,
                "fields": handle.fields,
                "preview": handle.preview,
                "summary": handle.summary,
                "freshness": handle.freshness,
                "history": {
                    "mode": (
                        "local_rolling_history"
                        if query_spec.get("window")
                        else "current_snapshot"
                    ),
                    "requested_window": query_spec.get("window"),
                    "maximum_window": "P2D",
                    "oldest": handle.freshness.get("oldest"),
                    "newest": handle.freshness.get("newest"),
                },
            }
            _stored_handle, result_rows = self.query.load(handle.handle_id)
            result_item["panel_compatibility"] = compatibility_summary(handle.kind, result_rows)
            if handle.kind == "series":
                profile = result_item["panel_compatibility"]["profile"]
                result_item["series_profile"] = {
                    "distinct_timestamps": profile["distinct_timestamps"],
                    "series_count": profile["series_count"],
                    "series_with_history": profile["series_with_history"],
                    "max_points_per_series": profile["max_points_per_series"],
                    "supports_timeseries": "timeseries"
                    in result_item["panel_compatibility"]["recommended_panels"],
                    "requested_window": query_spec.get("window"),
                    "history_mode": (
                        "local_rolling_history"
                        if query_spec.get("window")
                        else "current_snapshot"
                    ),
                    "recommended_panel": (
                        "timeseries"
                        if "timeseries" in result_item["panel_compatibility"]["recommended_panels"]
                        else "ranking_or_map"
                    ),
                }
            results.append(result_item)
        health = {
            item.stream_id: {
                "stream_id": item.stream_id,
                "status": item.status,
                "provider": item.provider,
                "newest_record_at": item.newest_record_at,
                "record_count": item.record_count,
                "fixture": item.fixture,
                "fallback": item.fallback,
            }
            for item in self.sources.health()
            if not queried_streams or item.stream_id in queried_streams
        }
        return {
            "results": results,
            "aliases": aliases,
            "source_status": list(health.values()),
        }

    async def _tool_dashboard_batch(
        self, payload: dict[str, Any], *, session: Session | None = None
    ) -> dict[str, Any]:
        expected = int(payload["expected_version"])
        operations = [self._dashboard_operation(item) for item in payload.get("operations", [])]
        current = self.dashboard.current(session=session)
        assert current is not None
        operations, auto_removed = self._compose_dashboard_operations(
            str(payload.get("composition_mode", "adaptive")), operations, current
        )
        spec = self.dashboard.apply(
            expected,
            operations,
            created_by="agent",
            reason=str(payload.get("reason", "agent update"))[:240],
            conversation_id=payload.get("conversation_id"),
            source_bundle_version=self.sources.latest_bundle_version(),
            session=session,
        )
        return {
            "dashboard": spec.model_dump(mode="json"),
            "status": "pending_render",
            "composition_mode": str(payload.get("composition_mode", "adaptive")),
            "auto_removed_panel_ids": auto_removed,
        }

    def _compose_dashboard_operations(
        self,
        mode: str,
        operations: list[DashboardOperation],
        current: Any,
    ) -> tuple[list[DashboardOperation], list[str]]:
        if mode not in {"adaptive", "merge", "replace_visible"}:
            raise ToolExecutionError(
                "INVALID_COMPOSITION_MODE",
                "composition_mode moet adaptive, merge of replace_visible zijn.",
            )
        current_panels = {panel.panel_id: panel for panel in current.panels}
        touched_ids = {
            operation.panel.panel_id
            for operation in operations
            if operation.op == "upsert_panel" and operation.panel is not None
        }
        removed_ids = {
            operation.panel_id
            for operation in operations
            if operation.op == "remove_panel" and operation.panel_id
        }
        auto_removed: list[str] = []
        composed = list(operations)
        if mode == "replace_visible":
            replacements = [
                DashboardOperation(op="remove_panel", panel_id=panel.panel_id)
                for panel in current.panels
                if panel.panel_type not in self.HIDDEN_PANEL_TYPES
                and panel.panel_id not in touched_ids
                and panel.panel_id not in removed_ids
            ]
            auto_removed.extend(
                operation.panel_id for operation in replacements if operation.panel_id
            )
            composed = replacements + composed

        projected = dict(current_panels)
        for operation in composed:
            if operation.op == "remove_panel" and operation.panel_id:
                projected.pop(operation.panel_id, None)
            elif operation.op == "upsert_panel" and operation.panel:
                projected[operation.panel.panel_id] = operation.panel
        visible = [
            panel for panel in projected.values() if panel.panel_type not in self.HIDDEN_PANEL_TYPES
        ]
        if len(visible) > self.MAX_VISIBLE_PANELS and mode == "adaptive":
            removable = sorted(
                (panel for panel in visible if panel.panel_id not in touched_ids),
                key=lambda panel: (panel.order, panel.panel_id),
                reverse=True,
            )
            while len(visible) > self.MAX_VISIBLE_PANELS and removable:
                panel = removable.pop(0)
                composed.insert(0, DashboardOperation(op="remove_panel", panel_id=panel.panel_id))
                auto_removed.append(panel.panel_id)
                visible = [item for item in visible if item.panel_id != panel.panel_id]
        if len(visible) > self.MAX_VISIBLE_PANELS:
            raise ToolExecutionError(
                "PANEL_LIMIT_EXCEEDED",
                "Een dashboard mag maximaal twaalf zichtbare panelen bevatten.",
                details={
                    "maximum": self.MAX_VISIBLE_PANELS,
                    "projected": len(visible),
                    "hint": "Gebruik replace_visible of verwijder bestaande panelen in dezelfde batch.",
                },
            )
        return composed, list(dict.fromkeys(auto_removed))

    def _dashboard_operation(self, item: dict[str, Any]) -> DashboardOperation:
        if item.get("op") == "set_meta" and (item.get("values") or {}).get("layout_template"):
            return DashboardOperation.model_validate({**item, "op": "set_layout_template"})
        if item.get("op") == "set_map_mode":
            values = dict(item.get("values") or {})
            for key in ("panel_id", "panel_type"):
                if key not in values and item.get(key) is not None:
                    values[key] = item[key]
            return DashboardOperation.model_validate({**item, "values": values})
        if item.get("op") != "upsert_panel" or item.get("panel"):
            return DashboardOperation.model_validate(item)
        panel_data = {key: value for key, value in item.items() if key != "op"}
        binding_data = panel_data.pop("binding", None)
        bindings_data = panel_data.pop("bindings", None)
        seed_handle_id = panel_data.pop("seed_handle_id", None)
        if seed_handle_id:
            binding_data = dict(binding_data or {})
            binding_data.setdefault("seed_handle_id", seed_handle_id)
        if bindings_data is not None and binding_data:
            raise ToolExecutionError(
                "AMBIGUOUS_BINDINGS",
                "Gebruik binding voor een enkelvoudig panel of bindings voor een samengestelde weergave, niet beide.",
            )
        if bindings_data is not None:
            if not isinstance(bindings_data, list) or not 1 <= len(bindings_data) <= 6:
                raise ToolExecutionError(
                    "INVALID_PANEL_BINDING_COUNT",
                    "Een samengesteld panel ondersteunt een tot zes databindings.",
                )
            if panel_data.get("panel_type") not in MULTI_BINDING_PANEL_TYPES:
                raise ToolExecutionError(
                    "MULTI_BINDING_UNSUPPORTED",
                    f"Meerdere bindings worden niet ondersteund door {panel_data.get('panel_type')}.",
                )
            raw_bindings = bindings_data
        else:
            raw_bindings = [binding_data] if binding_data else []

        panel_type = str(panel_data.get("panel_type") or "")
        bindings = [
            self._logical_binding(
                data,
                panel_type,
                allow_comparison_scalar=len(raw_bindings) > 1,
            )
            for data in raw_bindings
        ]
        query_hashes = [binding.query_hash for binding in bindings]
        if len(query_hashes) != len(set(query_hashes)):
            raise ToolExecutionError(
                "DUPLICATE_PANEL_BINDING",
                "Dezelfde bronquery mag maar eenmaal aan een panel binden.",
            )
        if panel_type == "comparison" and len(bindings) > 1:
            numeric_values = 0
            for data in raw_bindings:
                handle_id = data.get("seed_handle_id") or data.get("handle_id")
                rows = list(self.query.load_payload(str(handle_id)).get("rows") or [])
                numeric_values += sum(
                    isinstance(row.get("current", row.get("value")), (int, float)) for row in rows
                )
            if numeric_values < 2:
                raise ToolExecutionError(
                    "COMPARISON_REQUIRES_VALUES",
                    "Een samengestelde vergelijking vereist minstens twee numerieke waarden.",
                    details={"available_values": numeric_values, "required_values": 2},
                )
        panel = PanelSpec.model_validate(
            {
                **panel_data,
                "binding": bindings[0] if bindings else None,
                "layer_bindings": bindings[1:],
            }
        )
        return DashboardOperation(op="upsert_panel", panel=panel)

    def _logical_binding(
        self,
        binding_data: dict[str, Any],
        panel_type: str,
        *,
        allow_comparison_scalar: bool = False,
    ) -> LogicalDataBinding:
        if not isinstance(binding_data, dict):
            raise ToolExecutionError("INVALID_BINDING", "Binding moet een object zijn.")
        handle_id = binding_data.get("seed_handle_id") or binding_data.get("handle_id")
        if not handle_id:
            raise ToolExecutionError("BINDING_HANDLE_REQUIRED", "Binding vereist seed_handle_id")
        payload = self.query.load_payload(str(handle_id))
        handle = payload["handle"]
        query_spec = payload.get("query_spec")
        if not query_spec:
            raise ToolExecutionError("BINDING_QUERY_MISSING", "Handle bevat geen logical query")
        field_bindings = binding_data.get("field_bindings") or {}
        fields = set(handle["fields"])
        invalid = [value for value in field_bindings.values() if value.split(".")[0] not in fields]
        if invalid:
            raise ToolExecutionError(
                "INVALID_FIELD_BINDING", f"Onbekende handlevelden: {', '.join(invalid)}"
            )
        rows = list(payload.get("rows") or [])
        compatibility = evaluate_panel(panel_type, str(handle["kind"]), rows, field_bindings)
        if panel_type == "comparison" and allow_comparison_scalar and not compatibility.compatible:
            compatibility = evaluate_panel("kpi", str(handle["kind"]), rows, field_bindings)
        if not compatibility.compatible:
            details = {
                "panel_type": panel_type,
                "handle_kind": handle["kind"],
                **compatibility.details,
                "alternatives": compatibility_summary(str(handle["kind"]), rows)[
                    "recommended_panels"
                ],
            }
            if compatibility.code == "INSUFFICIENT_SERIES":
                details["available_timestamps"] = compatibility.details["profile"][
                    "max_points_per_series"
                ]
                details["required_timestamps"] = 2
            raise ToolExecutionError(compatibility.code, compatibility.message, details=details)
        return LogicalDataBinding(
            binding_id=f"bnd_{uuid4().hex[:20]}",
            kind=handle["kind"],
            query_hash=handle["query_hash"],
            query_spec=query_spec,
            field_bindings=field_bindings,
            refresh_policy=binding_data.get("refresh_policy", "visible"),
            schema_fingerprint=handle["schema_fingerprint"],
        )

    async def _tool_nearby_places(self, payload: dict[str, Any]) -> dict[str, Any]:
        origin_handle = payload.get("origin_handle")
        resolution_id = payload.get("resolution_id")
        location_ref = payload.get("location_ref")
        origin_text = str(payload.get("origin_text") or "").strip()
        if isinstance(origin_handle, str) and origin_handle.startswith("locres_"):
            resolution_id, origin_handle = origin_handle, None
        if isinstance(location_ref, str) and location_ref.startswith("locres_"):
            resolution_id, location_ref = location_ref, None
        origin_label: str | None = None
        if origin_handle:
            handle, rows = self.query.load(str(origin_handle))
            bundle_version = handle.source_bundle_version
            origin_reference = handle.handle_id
        elif resolution_id:
            try:
                resolution = self.locations.get(str(resolution_id))
            except KeyError as exc:
                raise ToolExecutionError(
                    "LOCATION_RESOLUTION_EXPIRED",
                    "De tijdelijke locatieresolutie is onbekend of verlopen; resolveer de plaats opnieuw.",
                    retryable=True,
                ) from exc
            rows = [
                {"location": {"latitude": resolution.latitude, "longitude": resolution.longitude}}
            ]
            bundle_version = self.query.latest_bundle()
            origin_reference = resolution.resolution_id
            origin_label = resolution.display_label
        elif origin_text:
            try:
                geocoded = await self.geocoding.resolve(origin_text)
            except RuntimeError as exc:
                if str(exc) == "GOOGLE_GEOCODING_NOT_CONFIGURED":
                    raise ToolExecutionError(
                        "GOOGLE_GEOCODING_NOT_CONFIGURED",
                        "Google Geocoding is niet geconfigureerd voor plaatsnamen.",
                    ) from exc
                raise
            matches = geocoded.get("matches") or []
            if not matches:
                raise ToolExecutionError(
                    "LOCATION_NOT_RESOLVED",
                    "Google vond geen eenduidige locatie; verduidelijk de plaats of het adres.",
                )
            resolution = self.locations.put(origin_text, matches[0])
            rows = [
                {"location": {"latitude": resolution.latitude, "longitude": resolution.longitude}}
            ]
            bundle_version = self.query.latest_bundle()
            origin_reference = resolution.resolution_id
            origin_label = resolution.display_label
        elif location_ref:
            handle = self.query.execute({"operation": "query", "limit": 2000})
            _, all_rows = self.query.load(handle.handle_id)
            rows = [
                row
                for row in all_rows
                if (row.get("location") or {}).get("location_id") == location_ref
            ]
            bundle_version = handle.source_bundle_version
            origin_reference = str(location_ref)
        else:
            raise ToolExecutionError(
                "ORIGIN_REQUIRED",
                "origin_text, origin_handle, bestaande location_ref of tijdelijke resolution_id is verplicht",
            )
        location = next((row.get("location") for row in rows if row.get("location")), None)
        if not location:
            raise ToolExecutionError("NO_LOCATION", "origin handle has no trusted location")
        origin_latitude = float(location["latitude"])
        origin_longitude = float(location["longitude"])
        raw_types = payload.get("included_types") or ["hospital"]
        if not isinstance(raw_types, list):
            raise ToolExecutionError(
                "INVALID_PLACE_TYPES",
                "included_types moet een lijst met voorzieningstypen zijn.",
            )
        requested_types = list(dict.fromkeys(str(place_type) for place_type in raw_types))
        allowed_types = set(NEARBY_PLACE_TYPES)
        included_types = [
            place_type for place_type in requested_types if place_type in allowed_types
        ]
        ignored_types = [
            str(place_type) for place_type in requested_types if place_type not in included_types
        ]
        if not included_types:
            raise ToolExecutionError(
                "UNSUPPORTED_PLACE_TYPES",
                "Geen van de gevraagde voorzieningstypen wordt ondersteund.",
                details={
                    "requested_types": requested_types,
                    "allowed_types": sorted(allowed_types),
                },
            )
        try:
            data = await self.places.nearby(
                latitude=origin_latitude,
                longitude=origin_longitude,
                included_types=included_types,
                radius_m=int(payload.get("radius_m", 25_000)),
                max_results=int(payload.get("max_results", 15)),
                rank=payload.get("rank", "distance"),
                fields_profile=payload.get("fields_profile", "minimal"),
            )
        except RuntimeError as exc:
            if str(exc) == "GOOGLE_PLACES_NOT_CONFIGURED":
                raise ToolExecutionError(
                    "GOOGLE_PLACES_NOT_CONFIGURED",
                    "Google Places is niet geconfigureerd voor voorzieningen in de buurt.",
                ) from exc
            raise
        place_rows = [
            {
                "record_id": item.get("id"),
                "title": (item.get("displayName") or {}).get("text") or item.get("id"),
                "primary_type": item.get("primaryType"),
                "location": {
                    "latitude": (item.get("location") or {}).get("latitude"),
                    "longitude": (item.get("location") or {}).get("longitude"),
                },
                "google_maps_uri": item.get("googleMapsUri"),
                "attribution": "Google Maps",
                "distance_m": round(
                    haversine_m(
                        origin_latitude,
                        origin_longitude,
                        float((item.get("location") or {})["latitude"]),
                        float((item.get("location") or {})["longitude"]),
                    )
                ),
            }
            for item in data["places"]
            if (item.get("location") or {}).get("latitude") is not None
            and (item.get("location") or {}).get("longitude") is not None
        ]
        if payload.get("rank", "distance") == "distance":
            place_rows.sort(key=lambda item: item["distance_m"])
        origin_row = {
            "record_id": str(origin_reference),
            "title": origin_label or "Geselecteerde locatie",
            "description": "Gegeocodeerde oorsprong van de ruimtelijke zoekopdracht.",
            "category": "search_origin",
            "primary_type": "origin",
            "location": {
                "latitude": origin_latitude,
                "longitude": origin_longitude,
            },
            "attribution": "Google Maps",
            "distance_m": 0,
            "is_origin": True,
        }
        map_rows = [origin_row, *place_rows]
        places_handle = self.query.create_handle(
            "places",
            {
                "operation": "nearby_places",
                "origin": origin_reference,
                **data["request"],
            },
            map_rows,
            bundle_version,
        )
        return {
            "origin": {"reference": origin_reference, "label": origin_label},
            "places_handle": places_handle.model_dump(mode="json"),
            "preview": place_rows[:5],
            "nearest": place_rows[0] if place_rows else None,
            "attribution": data["attribution"],
            "budget": data["budget"],
            "warnings": (
                [
                    {
                        "code": "PLACE_TYPES_IGNORED",
                        "message": "Niet-ondersteunde voorzieningstypen zijn overgeslagen.",
                        "ignored_types": ignored_types,
                        "used_types": included_types,
                    }
                ]
                if ignored_types
                else []
            ),
        }

    async def _tool_capture_dashboard(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.capture is None:
            raise ToolExecutionError("CAPTURE_UNAVAILABLE", "capture service is not configured")
        return await self.capture(payload)

    async def _tool_external_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.policy()["web_search_enabled"]:
            raise ToolExecutionError(
                "POLICY_DISABLED", "Websearch staat uit; alleen de gebruiker kan dit inschakelen."
            )
        results = await self.search.search(
            str(payload["query"]),
            max_results=min(int(payload.get("max_results", 5)), 5),
            recency_days=payload.get("recency_days"),
        )
        domains = {str(value).casefold() for value in payload.get("domain_allowlist") or []}
        if domains:
            results = [
                item
                for item in results
                if any(domain in str(item.get("url", "")).casefold() for domain in domains)
            ]
        handle = self.query.create_handle(
            "web_results",
            {
                "operation": "external_search",
                "query": payload["query"],
                "recency_days": payload.get("recency_days"),
            },
            results,
        )
        return {
            "handle": handle.model_dump(mode="json"),
            "preview": results[:5],
            "trust_tier": "unverified_external",
            "retrieved_at": datetime.now(UTC).isoformat(),
        }

    def policy(self) -> dict[str, Any]:
        with self.database.session() as session:
            row = session.get(SessionPolicyRow, 1)
            if row is None:
                row = SessionPolicyRow(
                    singleton_id=1,
                    version=1,
                    web_search_enabled=False,
                    auto_update_enabled=True,
                    updated_at=datetime.now(UTC).isoformat(),
                )
                session.add(row)
                session.flush()
            return {
                "version": row.version,
                "web_search_enabled": row.web_search_enabled,
                "auto_update_enabled": row.auto_update_enabled,
            }

    def update_policy(
        self, *, web_search_enabled: bool | None = None, auto_update_enabled: bool | None = None
    ) -> dict[str, Any]:
        with self.database.session() as session:
            row = session.get(SessionPolicyRow, 1)
            if row is None:
                row = SessionPolicyRow(
                    singleton_id=1,
                    version=1,
                    web_search_enabled=False,
                    auto_update_enabled=True,
                    updated_at=datetime.now(UTC).isoformat(),
                )
                session.add(row)
            if web_search_enabled is not None:
                row.web_search_enabled = web_search_enabled
            if auto_update_enabled is not None:
                row.auto_update_enabled = auto_update_enabled
            row.version += 1
            row.updated_at = datetime.now(UTC).isoformat()
            session.flush()
            return {
                "version": row.version,
                "web_search_enabled": row.web_search_enabled,
                "auto_update_enabled": row.auto_update_enabled,
            }
