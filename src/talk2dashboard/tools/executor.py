from __future__ import annotations

import json
import math
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from statistics import mean
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from talk2dashboard.dashboard import DashboardService
from talk2dashboard.domain import (
    DashboardOperation,
    LogicalDataBinding,
    PanelSpec,
    ToolRequest,
    ToolResponse,
)
from talk2dashboard.integrations.places import GeocodingClient, PlacesClient
from talk2dashboard.integrations.search import BraveSearchClient
from talk2dashboard.query import QueryEngine
from talk2dashboard.sources.service import SourceService
from talk2dashboard.storage.database import Database
from talk2dashboard.storage.models import IncidentClusterRow, SessionPolicyRow, ToolAuditRow

CaptureCallable = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class ToolExecutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ToolExecutor:
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

    async def execute(self, name: str, request: ToolRequest) -> ToolResponse:
        with self.database.session() as session:
            existing = session.scalars(
                select(ToolAuditRow).where(ToolAuditRow.request_id == request.request_id)
            ).first()
            if existing:
                return ToolResponse(
                    ok=existing.ok,
                    result=json.loads(existing.result_json) if existing.result_json else None,
                    error={"code": existing.error_code, "message": "Idempotent replay"}
                    if not existing.ok
                    else None,
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
        except ToolExecutionError as exc:
            ok, error = False, {"code": exc.code, "message": str(exc)}
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
            request_id=request.request_id,
            tool_name=name,
            arguments_json=json.dumps(request.payload, ensure_ascii=True),
            result_json=json.dumps(result, ensure_ascii=True, default=str)
            if result is not None
            else None,
            ok=ok,
            error_code=error["code"] if error else None,
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
        sections = set(payload.get("sections") or [])
        aliases = {
            "stream_schema": "schemas",
            "panel_types": "panels",
            "policies": "policy",
        }
        sections = {aliases.get(item, item) for item in sections}
        output: dict[str, Any] = {}
        if "streams" in sections or "schemas" in sections:
            output["streams"] = [item.model_dump(mode="json") for item in self.sources.health()]
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
                "filter_ops": ["eq", "in", "gte", "lte", "between", "contains"],
            }
        if "panels" in sections:
            output["panel_types"] = {
                "kpi": ["aggregate", "baseline"],
                "timeseries": ["series"],
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
            output["dashboard"] = spec.model_dump(mode="json") if spec else None
        if "policy" in sections:
            output["policy"] = self.policy()
        if "incidents" in sections:
            with self.database.session() as session:
                rows = session.scalars(
                    select(IncidentClusterRow)
                    .order_by(IncidentClusterRow.last_seen_at.desc())
                    .limit(20)
                ).all()
                output["incidents"] = [json.loads(row.payload_json) for row in rows]
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

    async def _tool_data_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        operations = payload.get("operations") or []
        if not isinstance(operations, list) or not 1 <= len(operations) <= 12:
            raise ToolExecutionError("INVALID_BATCH", "operations must contain one to twelve items")
        aliases: dict[str, str] = {}
        results = []
        for index, operation in enumerate(operations):
            query_spec = self._resolve_aliases(dict(operation.get("query") or operation), aliases)
            save_as = query_spec.pop("save_as", operation.get("save_as", f"result_{index + 1}"))
            operation_name = query_spec.get("operation", "query")
            if operation_name == "get_incident":
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
                source_handle = self.query.execute(
                    {"operation": "query_events", "text": text, "limit": 20}
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
                    rows = [
                        {
                            "record_id": f"ephemeral:{item.get('place_id')}",
                            "title": item.get("display_label"),
                            "location": {
                                "latitude": (item.get("location") or {}).get("lat"),
                                "longitude": (item.get("location") or {}).get("lng"),
                            },
                            "google_place_id": item.get("place_id"),
                            "temporary": True,
                            "attribution": "Google Maps",
                        }
                        for item in matches
                    ]
                    handle = self.query.create_handle(
                        "location",
                        query_spec,
                        rows,
                        source_handle.source_bundle_version,
                        ttl_seconds=900,
                    )
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
                    raise ToolExecutionError(
                        "INSUFFICIENT_SERIES", "Correlatie vereist drie gekoppelde waarden."
                    )
                aa, bb = left_values[-count:], right_values[-count:]
                mean_a, mean_b = mean(aa), mean(bb)
                numerator = sum((a - mean_a) * (b - mean_b) for a, b in zip(aa, bb, strict=True))
                denominator = math.sqrt(
                    sum((value - mean_a) ** 2 for value in aa)
                    * sum((value - mean_b) ** 2 for value in bb)
                )
                handle = self.query.create_handle(
                    "correlation",
                    query_spec,
                    [
                        {
                            "series_a": left.handle_id,
                            "series_b": query_spec["series_b"],
                            "correlation": numerator / denominator if denominator else 0.0,
                            "sample_size": count,
                            "causality": False,
                        }
                    ],
                    left.source_bundle_version,
                )
            else:
                handle = self.query.execute(query_spec)
            aliases[str(save_as)] = handle.handle_id
            results.append(
                {
                    "alias": save_as,
                    "handle_id": handle.handle_id,
                    "kind": handle.kind,
                    "row_count": handle.row_count,
                    "fields": handle.fields,
                    "preview": handle.preview,
                    "summary": handle.summary,
                    "freshness": handle.freshness,
                }
            )
        return {"results": results, "aliases": aliases}

    async def _tool_dashboard_batch(
        self, payload: dict[str, Any], *, session: Session | None = None
    ) -> dict[str, Any]:
        expected = int(payload["expected_version"])
        operations = [self._dashboard_operation(item) for item in payload.get("operations", [])]
        spec = self.dashboard.apply(
            expected,
            operations,
            created_by="agent",
            reason=str(payload.get("reason", "agent update"))[:240],
            conversation_id=payload.get("conversation_id"),
            session=session,
        )
        return {"dashboard": spec.model_dump(mode="json"), "status": "pending_render"}

    def _dashboard_operation(self, item: dict[str, Any]) -> DashboardOperation:
        if item.get("op") != "upsert_panel" or item.get("panel"):
            return DashboardOperation.model_validate(item)
        panel_data = {key: value for key, value in item.items() if key != "op"}
        binding_data = panel_data.pop("binding", None)
        seed_handle_id = panel_data.pop("seed_handle_id", None)
        if seed_handle_id and binding_data is None:
            binding_data = {"seed_handle_id": seed_handle_id}
        binding = None
        if binding_data:
            handle_id = binding_data.get("seed_handle_id") or binding_data.get("handle_id")
            if not handle_id:
                raise ToolExecutionError(
                    "BINDING_HANDLE_REQUIRED", "Binding vereist seed_handle_id"
                )
            payload = self.query.load_payload(str(handle_id))
            handle = payload["handle"]
            query_spec = payload.get("query_spec")
            if not query_spec:
                raise ToolExecutionError("BINDING_QUERY_MISSING", "Handle bevat geen logical query")
            field_bindings = binding_data.get("field_bindings") or {}
            fields = set(handle["fields"])
            invalid = [
                value for value in field_bindings.values() if value.split(".")[0] not in fields
            ]
            if invalid:
                raise ToolExecutionError(
                    "INVALID_FIELD_BINDING", f"Onbekende handlevelden: {', '.join(invalid)}"
                )
            binding = LogicalDataBinding(
                binding_id=f"bnd_{uuid4().hex[:20]}",
                kind=handle["kind"],
                query_hash=handle["query_hash"],
                query_spec=query_spec,
                field_bindings=field_bindings,
                refresh_policy=binding_data.get("refresh_policy", "visible"),
                schema_fingerprint=handle["schema_fingerprint"],
            )
        panel = PanelSpec.model_validate({**panel_data, "binding": binding})
        return DashboardOperation(op="upsert_panel", panel=panel)

    async def _tool_nearby_places(self, payload: dict[str, Any]) -> dict[str, Any]:
        origin_handle = payload.get("origin_handle")
        if origin_handle:
            handle, rows = self.query.load(str(origin_handle))
        elif payload.get("location_ref"):
            handle = self.query.execute({"operation": "query", "limit": 2000})
            _, all_rows = self.query.load(handle.handle_id)
            rows = [
                row
                for row in all_rows
                if (row.get("location") or {}).get("location_id") == payload["location_ref"]
            ]
        else:
            raise ToolExecutionError(
                "ORIGIN_REQUIRED", "origin_handle of bestaande location_ref is verplicht"
            )
        location = next((row.get("location") for row in rows if row.get("location")), None)
        if not location:
            raise ToolExecutionError("NO_LOCATION", "origin handle has no trusted location")
        data = await self.places.nearby(
            latitude=float(location["latitude"]),
            longitude=float(location["longitude"]),
            included_types=payload.get("included_types") or ["hospital"],
            radius_m=int(payload.get("radius_m", 5000)),
            max_results=int(payload.get("max_results", 10)),
            rank=payload.get("rank", "distance"),
            fields_profile=payload.get("fields_profile", "minimal"),
        )
        rows = [
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
            }
            for item in data["places"]
        ]
        places_handle = self.query.create_handle(
            "places",
            {
                "operation": "nearby_places",
                "origin_handle": handle.handle_id,
                **data["request"],
            },
            rows,
            handle.source_bundle_version,
        )
        return {
            "origin_handle": handle.handle_id,
            "places_handle": places_handle.model_dump(mode="json"),
            "preview": rows[:5],
            "attribution": data["attribution"],
            "budget": data["budget"],
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
