from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import jsonpatch
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from talk2dashboard.domain import (
    DashboardOperation,
    DashboardSpec,
    LogicalDataBinding,
    PanelSpec,
)
from talk2dashboard.errors import VersionConflictError
from talk2dashboard.query import QueryEngine, canonical_hash
from talk2dashboard.storage.database import Database
from talk2dashboard.storage.models import DashboardConfigRow, RenderStateRow


class DashboardService:
    # Provider-backed result sets cannot be reproduced by QueryEngine. They remain
    # immutable snapshots until an explicit tool call creates and binds a new handle.
    _SNAPSHOT_HANDLE_KINDS = frozenset({"places", "web_results"})

    def __init__(self, database: Database, query: QueryEngine) -> None:
        self.database = database
        self.query = query

    def ensure_default(self, bundle_version: str) -> DashboardSpec:
        existing = self.current(optional=True)
        if existing:
            return existing
        event_query = {"record_kind": "event", "limit": 100}
        event_handle = self.query.execute(event_query, bundle_version)
        wind_query = {"record_kind": "measurement", "metric": "wind_gust_kmh", "window": "PT60M"}
        wind_handle = self.query.execute(wind_query, bundle_version)
        panels = [
            PanelSpec(
                panel_id="source-health",
                panel_type="source_health",
                title="Bronstatus",
                span="full",
                order=0,
            ),
            PanelSpec(
                panel_id="incident-map",
                panel_type="map_3d_google",
                title="Incidentbeeld",
                span="wide",
                order=1,
                binding=self._binding(
                    "events-map",
                    event_query,
                    event_handle,
                    {"latitude": "location.latitude", "longitude": "location.longitude"},
                ),
            ),
            PanelSpec(
                panel_id="incident-timeline",
                panel_type="incident_timeline",
                title="Live meldingen",
                span="standard",
                order=2,
                binding=self._binding(
                    "events-timeline",
                    event_query,
                    event_handle,
                    {"time": "observed_at", "label": "title"},
                ),
            ),
            PanelSpec(
                panel_id="wind-trend",
                panel_type="ranking",
                title="Windstoten · actuele meetstations",
                span="wide",
                order=3,
                binding=self._binding(
                    "wind-trend",
                    wind_query,
                    wind_handle,
                    {"label": "location.label", "y": "value"},
                ),
                props={"unit": "km/h"},
            ),
            PanelSpec(
                panel_id="evidence",
                panel_type="evidence",
                title="Bronmix",
                span="standard",
                order=4,
                binding=self._binding("evidence", event_query, event_handle, {"label": "title"}),
            ),
        ]
        spec = DashboardSpec(
            created_from_source_bundle_version=bundle_version,
            panels=panels,
            reason="deterministic initial dashboard",
        )
        self._persist(spec, previous=None)
        return spec

    @staticmethod
    def _binding(name: str, query_spec: dict, handle, fields: dict[str, str]) -> LogicalDataBinding:
        return LogicalDataBinding(
            binding_id=f"bnd_{canonical_hash(name, 'id').split('_', 1)[1]}",
            kind=handle.kind,
            query_hash=handle.query_hash,
            query_spec=query_spec,
            field_bindings=fields,
            schema_fingerprint=handle.schema_fingerprint,
        )

    def current(
        self, optional: bool = False, *, session: Session | None = None
    ) -> DashboardSpec | None:
        if session is not None:
            row = self._current_row(session)
            if row is None:
                if optional:
                    return None
                raise RuntimeError("Dashboard is not initialized")
            return DashboardSpec.model_validate_json(row.spec_json)
        with self.database.session() as managed_session:
            return self.current(optional=optional, session=managed_session)

    @staticmethod
    def _current_row(session: Session) -> DashboardConfigRow | None:
        return session.scalars(
            select(DashboardConfigRow)
            .where(DashboardConfigRow.dashboard_id == "main")
            .order_by(DashboardConfigRow.version.desc())
        ).first()

    def history(self) -> list[dict]:
        with self.database.session() as session:
            rows = session.scalars(
                select(DashboardConfigRow)
                .where(DashboardConfigRow.dashboard_id == "main")
                .order_by(DashboardConfigRow.version.desc())
                .limit(50)
            ).all()
            return [
                {
                    "version": row.version,
                    "created_at": row.created_at,
                    "created_by": row.created_by,
                    "reason": row.reason,
                    "renderer_status": row.renderer_status,
                }
                for row in rows
            ]

    def get_version(self, version: int) -> DashboardSpec:
        with self.database.session() as session:
            row = session.scalars(
                select(DashboardConfigRow).where(
                    DashboardConfigRow.dashboard_id == "main",
                    DashboardConfigRow.version == version,
                )
            ).first()
            if row is None:
                raise KeyError(f"Unknown dashboard version: {version}")
            return DashboardSpec.model_validate_json(row.spec_json)

    def link_screenshot(self, version: int, screenshot_handle: str) -> None:
        with self.database.session() as session:
            row = session.scalars(
                select(DashboardConfigRow).where(
                    DashboardConfigRow.dashboard_id == "main",
                    DashboardConfigRow.version == version,
                )
            ).first()
            if row is None:
                raise KeyError(f"Unknown dashboard version: {version}")
            row.screenshot_handle = screenshot_handle

    def apply(
        self,
        expected_version: int,
        operations: list[DashboardOperation],
        *,
        created_by: str,
        reason: str,
        conversation_id: str | None = None,
        source_bundle_version: str | None = None,
        session: Session | None = None,
    ) -> DashboardSpec:
        if session is None:
            with self.database.session() as managed_session:
                return self.apply(
                    expected_version,
                    operations,
                    created_by=created_by,
                    reason=reason,
                    conversation_id=conversation_id,
                    source_bundle_version=source_bundle_version,
                    session=managed_session,
                )
        current = self.current(session=session)
        assert current is not None
        if current.version != expected_version:
            raise VersionConflictError(expected_version, current.version)
        if any(operation.op == "undo" for operation in operations):
            if len(operations) != 1:
                raise ValueError("undo cannot be combined with other operations")
            return self.undo(
                created_by=created_by,
                conversation_id=conversation_id,
                session=session,
            )
        data = current.model_dump(mode="python")
        panels = {panel["panel_id"]: panel for panel in data["panels"]}
        for operation in operations:
            if operation.op == "set_meta":
                for key in ("title", "subtitle", "refresh_focus"):
                    if key in operation.values:
                        data[key] = operation.values[key]
            elif operation.op == "set_layout_template":
                data["layout_template"] = operation.values["layout_template"]
            elif operation.op == "upsert_panel" and operation.panel:
                panels[operation.panel.panel_id] = operation.panel.model_dump(mode="python")
            elif operation.op == "remove_panel" and operation.panel_id:
                panels.pop(operation.panel_id, None)
            elif operation.op == "set_global_filter":
                data["global_filters"] = operation.values.get("filters", [])
            elif operation.op == "set_focus":
                data["map_focus"] = operation.values.get("map_focus")
            elif operation.op == "set_map_mode":
                panel_id = operation.values.get("panel_id", "incident-map")
                if panel_id in panels:
                    panels[panel_id]["panel_type"] = operation.values.get("panel_type", "map_2d")
        data.update(
            {
                "version": current.version + 1,
                "panels": sorted(panels.values(), key=lambda panel: panel.get("order", 0)),
                "created_at": datetime.now(UTC),
                "created_by": created_by,
                "conversation_id": conversation_id,
                "reason": reason,
                "created_from_source_bundle_version": (
                    source_bundle_version or current.created_from_source_bundle_version
                ),
            }
        )
        next_spec = DashboardSpec.model_validate(data)
        self._persist(next_spec, previous=current.version, session=session)
        return next_spec

    def undo(
        self,
        *,
        created_by: str,
        conversation_id: str | None,
        session: Session | None = None,
    ) -> DashboardSpec:
        if session is None:
            with self.database.session() as managed_session:
                return self.undo(
                    created_by=created_by,
                    conversation_id=conversation_id,
                    session=managed_session,
                )
        current = self.current(session=session)
        assert current is not None
        if current.version <= 1:
            raise ValueError("NO_UNDO_AVAILABLE")
        return self.restore(
            current.version - 1,
            created_by=created_by,
            conversation_id=conversation_id,
            reason="undo",
            session=session,
        )

    def restore(
        self,
        version: int,
        *,
        created_by: str = "user",
        conversation_id: str | None = None,
        reason: str = "restore",
        session: Session | None = None,
    ) -> DashboardSpec:
        if session is None:
            with self.database.session() as managed_session:
                return self.restore(
                    version,
                    created_by=created_by,
                    conversation_id=conversation_id,
                    reason=reason,
                    session=managed_session,
                )
        current = self.current(session=session)
        assert current is not None
        row = session.scalars(
            select(DashboardConfigRow).where(
                DashboardConfigRow.dashboard_id == "main", DashboardConfigRow.version == version
            )
        ).first()
        if row is None:
            raise KeyError(f"Unknown dashboard version: {version}")
        source = DashboardSpec.model_validate_json(row.spec_json)
        restored = source.model_copy(
            update={
                "version": current.version + 1,
                "created_at": datetime.now(UTC),
                "created_by": created_by,
                "conversation_id": conversation_id,
                "reason": reason,
            }
        )
        self._persist(
            restored,
            previous=current.version,
            restored_from=version,
            session=session,
        )
        return restored

    def materialize(
        self, spec: DashboardSpec, bundle_version: str | None = None
    ) -> dict[str, dict]:
        handles: dict[str, dict] = {}
        pending: dict[tuple[str, str], list[str]] = {}
        bindings: dict[tuple[str, str], LogicalDataBinding] = {}
        for panel in spec.panels:
            for layer_index, binding in enumerate(panel.bindings):
                if binding.refresh_policy == "paused":
                    continue
                target = (
                    panel.panel_id if layer_index == 0 else f"{panel.panel_id}::layer:{layer_index}"
                )
                if binding.kind in self._SNAPSHOT_HANDLE_KINDS:
                    snapshot = self.query.latest_compatible(
                        binding.query_hash, binding.schema_fingerprint
                    )
                    handles[target] = (
                        snapshot.model_dump(mode="json")
                        if snapshot
                        else {"error": "De externe snapshot is niet meer beschikbaar."}
                    )
                    continue
                key = (binding.query_hash, binding.schema_fingerprint)
                pending.setdefault(key, []).append(target)
                bindings[key] = binding

        if pending:
            with ThreadPoolExecutor(max_workers=min(8, len(pending))) as pool:
                futures = {
                    key: pool.submit(self._materialize_binding, binding, bundle_version)
                    for key, binding in bindings.items()
                }
                for key, panel_ids in pending.items():
                    materialized = futures[key].result()
                    for panel_id in panel_ids:
                        handles[panel_id] = materialized
        return handles

    def _materialize_binding(self, binding: LogicalDataBinding, bundle_version: str | None) -> dict:
        try:
            if bundle_version:
                cached = self.query.latest_compatible(
                    binding.query_hash,
                    binding.schema_fingerprint,
                    bundle_version=bundle_version,
                )
                if cached:
                    return cached.model_dump(mode="json")
            handle = self.query.execute(binding.query_spec, bundle_version)
            compatible = handle.schema_fingerprint == binding.schema_fingerprint
            if handle.row_count and compatible:
                return handle.model_dump(mode="json")
            previous = self.query.latest_compatible(
                binding.query_hash,
                binding.schema_fingerprint,
                exclude_bundle=bundle_version,
            )
            if previous:
                return {
                    **previous.model_dump(mode="json"),
                    "warning": "Nieuw bronresultaat is leeg of schema-incompatibel; vorige handle getoond.",
                }
            return {
                "error": "Geen compatibele datahandle beschikbaar.",
                "handle_id": handle.handle_id,
            }
        except (KeyError, ValueError, RuntimeError, OperationalError) as exc:
            previous = self.query.latest_compatible(binding.query_hash, binding.schema_fingerprint)
            return (
                {
                    **previous.model_dump(mode="json"),
                    "warning": f"Verversen mislukt ({type(exc).__name__}); vorige handle getoond.",
                }
                if previous
                else {"error": f"Databinding mislukt: {type(exc).__name__}"}
            )

    def acknowledge_render(
        self,
        dashboard_version: int,
        source_bundle_version: str,
        handle_ids: list[str],
        status: str,
        error: str | None = None,
    ) -> None:
        with self.database.session() as session:
            session.merge(
                RenderStateRow(
                    dashboard_id="main",
                    dashboard_version=dashboard_version,
                    source_bundle_version=source_bundle_version,
                    handle_ids_json=json.dumps(handle_ids),
                    status=status,
                    updated_at=datetime.now(UTC).isoformat(),
                    error=error,
                )
            )
            row = session.scalars(
                select(DashboardConfigRow).where(
                    DashboardConfigRow.dashboard_id == "main",
                    DashboardConfigRow.version == dashboard_version,
                )
            ).first()
            if row:
                row.renderer_status = status
                row.last_rendered_source_bundle_version = source_bundle_version
                row.last_rendered_handle_ids_json = json.dumps(handle_ids)

    def runtime_state(self) -> dict | None:
        with self.database.session() as session:
            row = session.get(RenderStateRow, "main")
            if row is None:
                return None
            return {
                "dashboard_version": row.dashboard_version,
                "source_bundle_version": row.source_bundle_version,
                "handle_ids": json.loads(row.handle_ids_json),
                "status": row.status,
                "updated_at": row.updated_at,
                "error": row.error,
            }

    def _persist(
        self,
        spec: DashboardSpec,
        previous: int | None,
        restored_from: int | None = None,
        *,
        session: Session | None = None,
    ) -> None:
        if session is None:
            with self.database.session() as managed_session:
                self._persist(
                    spec,
                    previous,
                    restored_from,
                    session=managed_session,
                )
            return
        previous_spec: dict = {}
        if previous is not None:
            previous_row = session.scalars(
                select(DashboardConfigRow).where(
                    DashboardConfigRow.dashboard_id == spec.dashboard_id,
                    DashboardConfigRow.version == previous,
                )
            ).first()
            if previous_row:
                previous_spec = json.loads(previous_row.spec_json)
        current_spec = spec.model_dump(mode="json")
        patch = jsonpatch.make_patch(previous_spec, current_spec).patch
        binding_ids = [binding.binding_id for panel in spec.panels for binding in panel.bindings]
        session.add(
            DashboardConfigRow(
                config_version_id=f"cfg_{uuid4().hex}",
                dashboard_id=spec.dashboard_id,
                version=spec.version,
                previous_version=previous,
                spec_json=spec.model_dump_json(),
                json_patch=json.dumps(patch, ensure_ascii=True),
                created_at=spec.created_at.isoformat(),
                created_by=spec.created_by,
                conversation_id=spec.conversation_id,
                turn_id=None,
                reason=spec.reason,
                source_bundle_version=spec.created_from_source_bundle_version,
                logical_binding_ids_json=json.dumps(binding_ids),
                last_rendered_source_bundle_version=None,
                last_rendered_handle_ids_json="[]",
                renderer_status="pending",
                screenshot_handle=None,
                restored_from_version=restored_from,
            )
        )
