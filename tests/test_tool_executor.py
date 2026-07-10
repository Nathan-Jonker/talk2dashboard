from __future__ import annotations

from sqlalchemy import func, select

from talk2dashboard.domain import ToolRequest
from talk2dashboard.integrations.places import GeocodingClient, PlacesClient
from talk2dashboard.integrations.search import BraveSearchClient
from talk2dashboard.storage.models import DashboardConfigRow, ToolAuditRow
from talk2dashboard.tools.executor import ToolExecutor


async def _executor(services):
    settings, database, sources, query, dashboard = services
    bundle = await sources.initialize_fixture()
    dashboard.ensure_default(bundle)
    return ToolExecutor(
        database,
        sources,
        query,
        dashboard,
        PlacesClient(settings, database),
        GeocodingClient(settings, database),
        BraveSearchClient(settings, database),
    )


async def test_one_data_batch_and_dashboard_batch_are_sufficient(services):
    executor = await _executor(services)
    policy = executor.policy()
    data = await executor.execute(
        "data_batch",
        ToolRequest(
            request_id="data-1",
            session_policy_version=policy["version"],
            dashboard_version=1,
            payload={
                "operations": [
                    {
                        "operation": "query_events",
                        "streams": ["p2000", "ndw_incidents"],
                        "window": "PT60M",
                        "limit": 50,
                        "save_as": "incidents",
                    }
                ]
            },
        ),
    )
    assert data.ok
    assert isinstance(data.result, dict)
    handle_id = data.result["aliases"]["incidents"]
    dashboard = await executor.execute(
        "dashboard_batch",
        ToolRequest(
            request_id="dash-1",
            session_policy_version=policy["version"],
            dashboard_version=1,
            payload={
                "expected_version": 1,
                "reason": "toon incidenten",
                "operations": [
                    {
                        "op": "upsert_panel",
                        "panel_id": "agent-incidents",
                        "panel_type": "map_2d",
                        "title": "Incidenten laatste uur",
                        "seed_handle_id": handle_id,
                        "binding": {
                            "seed_handle_id": handle_id,
                            "field_bindings": {
                                "latitude": "location.latitude",
                                "longitude": "location.longitude",
                            },
                        },
                        "span": "wide",
                        "order": 5,
                    }
                ],
            },
        ),
    )
    assert dashboard.ok
    assert isinstance(dashboard.result, dict)
    assert dashboard.dashboard_version == 2
    assert dashboard.result["dashboard"]["panels"][-1]["panel_id"] == "agent-incidents"


async def test_policy_version_conflict_is_explicit(services):
    executor = await _executor(services)
    response = await executor.execute(
        "inspect_workspace",
        ToolRequest(
            request_id="stale-policy",
            session_policy_version=999,
            payload={"sections": ["streams"]},
        ),
    )
    assert not response.ok
    assert response.error is not None
    assert response.error["code"] == "POLICY_VERSION_CONFLICT"


async def test_dashboard_batch_and_audit_share_one_transaction(services):
    executor = await _executor(services)
    policy = executor.policy()
    response = await executor.execute(
        "dashboard_batch",
        ToolRequest(
            request_id="atomic-dashboard",
            session_policy_version=policy["version"],
            dashboard_version=1,
            payload={
                "expected_version": 1,
                "reason": "atomic update",
                "operations": [{"op": "set_meta", "values": {"title": "Atomair dashboard"}}],
            },
        ),
    )
    assert response.ok
    _settings, database, _sources, _query, _dashboard = services
    with database.session() as session:
        config_count = session.scalar(select(func.count()).select_from(DashboardConfigRow))
        audit = session.scalars(
            select(ToolAuditRow).where(ToolAuditRow.request_id == "atomic-dashboard")
        ).one()
    assert config_count == 2
    assert audit.ok
    assert audit.dashboard_before == 1
    assert audit.dashboard_after == 2


async def test_failed_dashboard_batch_is_audited_without_new_version(services):
    executor = await _executor(services)
    policy = executor.policy()
    response = await executor.execute(
        "dashboard_batch",
        ToolRequest(
            request_id="atomic-dashboard-invalid",
            session_policy_version=policy["version"],
            dashboard_version=1,
            payload={
                "expected_version": 1,
                "operations": [
                    {
                        "op": "upsert_panel",
                        "panel_id": "invalid",
                        "panel_type": "map_2d",
                        "title": "Ongeldig",
                        "seed_handle_id": "hdl_does_not_exist",
                    }
                ],
            },
        ),
    )
    assert not response.ok
    _settings, database, _sources, _query, dashboard = services
    with database.session() as session:
        config_count = session.scalar(select(func.count()).select_from(DashboardConfigRow))
        audit = session.scalars(
            select(ToolAuditRow).where(ToolAuditRow.request_id == "atomic-dashboard-invalid")
        ).one()
    assert config_count == 1
    assert dashboard.current().version == 1
    assert not audit.ok
    assert audit.dashboard_before == audit.dashboard_after == 1
