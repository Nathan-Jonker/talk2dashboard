from __future__ import annotations

from datetime import timedelta

from talk2dashboard.integrations.places import GeocodingClient, PlacesClient
from talk2dashboard.integrations.search import BraveSearchClient
from talk2dashboard.planning import InitialDashboardPlanner
from talk2dashboard.tools.executor import ToolExecutor


class _FakeCerebras:
    configured = True

    async def plan_focus(self, _source_summary):
        return {
            "layout_template": "incident_focus",
            "title": "Actueel operationeel beeld",
            "subtitle": "Automatisch samengesteld uit de nieuwste bronnen",
            "focus_streams": ["knmi_observations", "p2000", "ndw_incidents"],
            "reason": "combinatie van weer en actuele meldingen",
        }


async def test_startup_planner_uses_persisted_cooldown_and_manual_override(services):
    settings, database, sources, query, dashboard = services
    bundle = await sources.initialize_fixture()
    dashboard.ensure_default(bundle)
    tools = ToolExecutor(
        database,
        sources,
        query,
        dashboard,
        PlacesClient(settings, database),
        GeocodingClient(settings, database),
        BraveSearchClient(settings, database),
    )
    planner = InitialDashboardPlanner(_FakeCerebras(), sources, dashboard, tools)  # type: ignore[arg-type]

    assert await planner.initialize()
    current = dashboard.current()
    assert current is not None
    assert current.version == 2
    assert current.created_from_source_bundle_version == bundle
    visible = [
        panel for panel in current.panels if panel.panel_type not in {"source_health", "evidence"}
    ]
    assert [panel.panel_id for panel in visible] == [
        "startup-knmi-observations",
        "startup-p2000",
        "startup-ndw-incidents",
    ]
    assert all(panel.binding is not None for panel in visible)
    ndw_panel = next(panel for panel in visible if panel.panel_id == "startup-ndw-incidents")
    assert ndw_panel.panel_type == "map_3d_google"

    # A new planner instance represents a server restart. The stored dashboard
    # timestamp, rather than process memory, must still enforce the cooldown.
    restarted = InitialDashboardPlanner(_FakeCerebras(), sources, dashboard, tools)  # type: ignore[arg-type]
    assert not await restarted.initialize(now=current.created_at + timedelta(minutes=14))
    status = restarted.cooldown_status(now=current.created_at + timedelta(minutes=14))
    assert status["reason"] == "cooldown_active"
    assert status["cooldown_minutes"] == 15

    assert await restarted.initialize(force=True)
    manual = dashboard.current()
    assert manual is not None
    assert manual.version == 3
    assert manual.reason.startswith("manual Cerebras composition:")

    elapsed = InitialDashboardPlanner(_FakeCerebras(), sources, dashboard, tools)  # type: ignore[arg-type]
    assert await elapsed.initialize(now=manual.created_at + timedelta(minutes=15, seconds=1))
    automatic = dashboard.current()
    assert automatic is not None
    assert automatic.version == 4
    assert automatic.reason.startswith("automatic Cerebras composition:")
