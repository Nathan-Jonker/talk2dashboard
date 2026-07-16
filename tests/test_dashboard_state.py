from __future__ import annotations

import json
import threading
import time

import jsonpatch
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from talk2dashboard.domain import DashboardOperation, LogicalDataBinding, PanelSpec
from talk2dashboard.storage.models import DashboardConfigRow


async def test_data_refresh_does_not_create_dashboard_version(services):
    _settings, _database, sources, _query, dashboard = services
    first_bundle = await sources.initialize_fixture()
    initial = dashboard.ensure_default(first_bundle)
    second_bundle = await sources.initialize_fixture()

    handles = dashboard.materialize(initial, second_bundle)
    current = dashboard.current()

    assert current is not None
    assert current.version == initial.version
    assert handles
    assert all(item["source_bundle_version"] == second_bundle for item in handles.values())


async def test_dashboard_materializes_unique_bindings_in_parallel(services, monkeypatch) -> None:
    _settings, _database, sources, query, dashboard = services
    bundle = await sources.initialize_fixture()
    spec = dashboard.ensure_default(bundle)
    original_execute = query.execute
    lock = threading.Lock()
    active = 0
    max_active = 0

    def delayed_execute(query_spec, bundle_version=None):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.05)
            return original_execute(query_spec, bundle_version)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(query, "execute", delayed_execute)

    materialized = dashboard.materialize(spec, bundle)

    assert max_active >= 2
    assert {"incident-map", "incident-timeline", "wind-trend"} <= set(materialized)
    assert next(panel for panel in spec.panels if panel.panel_id == "incident-map").panel_type == (
        "map_3d_google"
    )
    assert (
        materialized["incident-map"]["handle_id"] == materialized["incident-timeline"]["handle_id"]
    )


async def test_materialize_uses_previous_handles_during_sqlite_write_lock(services, monkeypatch):
    _settings, _database, sources, query, dashboard = services
    bundle = await sources.initialize_fixture()
    initial = dashboard.ensure_default(bundle)

    def locked_execute(*_args, **_kwargs):
        raise OperationalError("INSERT data_handles", {}, Exception("database is locked"))

    monkeypatch.setattr(query, "execute", locked_execute)
    handles = dashboard.materialize(initial, bundle)

    bound_panels = [panel for panel in initial.panels if panel.binding]
    assert set(handles) == {panel.panel_id for panel in bound_panels}
    assert all(item.get("handle_id") for item in handles.values())
    assert all("vorige handle" in item.get("warning", "") for item in handles.values())


async def test_materialize_reuses_external_places_snapshot_without_query_execution(
    services, monkeypatch
):
    _settings, _database, sources, query, dashboard = services
    bundle = await sources.initialize_fixture()
    initial = dashboard.ensure_default(bundle)
    query_spec = {
        "operation": "nearby_places",
        "origin": "Almere centrum",
        "included_types": ["hospital"],
    }
    handle = query.create_handle(
        "places",
        query_spec,
        [
            {
                "title": "Flevoziekenhuis",
                "distance_m": 750,
                "location": {"latitude": 52.3702, "longitude": 5.2141},
            }
        ],
        bundle,
    )
    places_panel = PanelSpec(
        panel_id="almere-hospitals",
        panel_type="map_3d_google",
        title="Ziekenhuizen in Almere",
        binding=LogicalDataBinding(
            binding_id="almere-hospitals",
            kind="places",
            query_hash=handle.query_hash,
            query_spec=query_spec,
            field_bindings={
                "label": "title",
                "latitude": "location.latitude",
                "longitude": "location.longitude",
            },
            schema_fingerprint=handle.schema_fingerprint,
        ),
    )
    spec = initial.model_copy(update={"panels": [*initial.panels, places_panel]})

    original_execute = query.execute

    def reject_places(query_spec, bundle_version=None):
        assert query_spec.get("operation") != "nearby_places"
        return original_execute(query_spec, bundle_version)

    monkeypatch.setattr(query, "execute", reject_places)
    handles = dashboard.materialize(spec, bundle)

    assert handles[places_panel.panel_id]["handle_id"] == handle.handle_id
    assert "warning" not in handles[places_panel.panel_id]


async def test_map_materializes_multiple_source_layers(services) -> None:
    _settings, _database, sources, query, dashboard = services
    bundle = await sources.initialize_fixture()
    initial = dashboard.ensure_default(bundle)
    map_panel = next(panel for panel in initial.panels if panel.panel_id == "incident-map")
    wind_query = {
        "record_kind": "measurement",
        "stream": "knmi_observations",
        "metric": "wind_gust_kmh",
        "limit": 25,
    }
    wind_handle = query.execute(wind_query, bundle)
    wind_binding = LogicalDataBinding(
        binding_id="bnd-map-knmi",
        kind=wind_handle.kind,
        query_hash=wind_handle.query_hash,
        query_spec=wind_query,
        field_bindings={
            "latitude": "location.latitude",
            "longitude": "location.longitude",
        },
        schema_fingerprint=wind_handle.schema_fingerprint,
    )
    layered_map = map_panel.model_copy(update={"layer_bindings": [wind_binding]})
    spec = initial.model_copy(
        update={
            "panels": [
                layered_map if panel.panel_id == layered_map.panel_id else panel
                for panel in initial.panels
            ]
        }
    )

    handles = dashboard.materialize(spec, bundle)

    assert handles["incident-map"]["kind"] == "events"
    assert handles["incident-map::layer:1"]["kind"] == "series"
    assert handles["incident-map"]["handle_id"] != handles["incident-map::layer:1"]["handle_id"]


def test_non_map_panel_accepts_layer_bindings_for_composable_views() -> None:
    binding = LogicalDataBinding(
        binding_id="bnd-extra",
        kind="series",
        query_hash="qry-extra",
        query_spec={"record_kind": "measurement"},
        field_bindings={"y": "value"},
        schema_fingerprint="schema-extra",
    )

    panel = PanelSpec(
        panel_id="ranking",
        panel_type="ranking",
        title="Ranglijst",
        binding=binding.model_copy(update={"binding_id": "bnd-primary"}),
        layer_bindings=[binding],
    )

    assert len(panel.bindings) == 2


def test_correlation_rejects_multiple_bindings() -> None:
    binding = LogicalDataBinding(
        binding_id="bnd-correlation-extra",
        kind="correlation",
        query_hash="qry-correlation-extra",
        query_spec={"operation": "correlate"},
        field_bindings={},
        schema_fingerprint="schema-correlation-extra",
    )

    with pytest.raises(ValidationError, match="not supported by correlation"):
        PanelSpec(
            panel_id="correlation",
            panel_type="correlation",
            title="Samenhang",
            binding=binding.model_copy(update={"binding_id": "bnd-correlation-primary"}),
            layer_bindings=[binding],
        )


async def test_view_update_is_versioned_and_undoable(services):
    _settings, _database, sources, _query, dashboard = services
    bundle = await sources.initialize_fixture()
    initial = dashboard.ensure_default(bundle)

    changed = dashboard.apply(
        initial.version,
        [DashboardOperation(op="set_meta", values={"title": "Wind en incidenten"})],
        created_by="agent",
        reason="test update",
    )
    restored = dashboard.undo(created_by="user", conversation_id=None)

    assert changed.version == 2
    assert changed.title == "Wind en incidenten"
    assert restored.version == 3
    assert restored.title == initial.title
    assert len(dashboard.history()) == 3

    with _database.session() as session:
        first, second = session.scalars(
            select(DashboardConfigRow).order_by(DashboardConfigRow.version.asc()).limit(2)
        ).all()
    reconstructed = jsonpatch.apply_patch(
        json.loads(first.spec_json), json.loads(second.json_patch)
    )
    assert reconstructed == json.loads(second.spec_json)


async def test_undo_cannot_be_combined(services):
    _settings, _database, sources, _query, dashboard = services
    bundle = await sources.initialize_fixture()
    initial = dashboard.ensure_default(bundle)

    try:
        dashboard.apply(
            initial.version,
            [
                DashboardOperation(op="undo"),
                DashboardOperation(op="set_meta", values={"title": "X"}),
            ],
            created_by="agent",
            reason="invalid",
        )
    except ValueError as exc:
        assert "cannot be combined" in str(exc)
    else:
        raise AssertionError("combined undo must fail")
