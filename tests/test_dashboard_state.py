from __future__ import annotations

import json

import jsonpatch
from sqlalchemy import select

from talk2dashboard.domain import DashboardOperation
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
