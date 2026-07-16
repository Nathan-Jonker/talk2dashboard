from __future__ import annotations

import threading
import time
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from talk2dashboard.domain import ToolRequest
from talk2dashboard.integrations.places import GeocodingClient, PlacesClient
from talk2dashboard.integrations.search import BraveSearchClient
from talk2dashboard.storage.models import DashboardConfigRow, ToolAuditRow
from talk2dashboard.tools.executor import ToolExecutionError, ToolExecutor


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
    assert {item["stream_id"] for item in data.result["source_status"]} == {
        "p2000",
        "ndw_incidents",
    }
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


async def test_dashboard_batch_accepts_multiple_map_source_bindings(services):
    executor = await _executor(services)
    policy = executor.policy()
    data = await executor.execute(
        "data_batch",
        ToolRequest(
            request_id="multi-map-data",
            session_policy_version=policy["version"],
            dashboard_version=1,
            payload={
                "operations": [
                    {
                        "operation": "query_events",
                        "stream": "ndw_incidents",
                        "limit": 25,
                        "save_as": "traffic",
                    },
                    {
                        "operation": "query_measurements",
                        "stream": "knmi_observations",
                        "metric": "wind_gust_kmh",
                        "limit": 25,
                        "save_as": "wind",
                    },
                ]
            },
        ),
    )
    assert data.ok and isinstance(data.result, dict)

    response = await executor.execute(
        "dashboard_batch",
        ToolRequest(
            request_id="multi-map-dashboard",
            session_policy_version=policy["version"],
            dashboard_version=1,
            payload={
                "expected_version": 1,
                "reason": "combineer verkeer en weer op een kaart",
                "operations": [
                    {
                        "op": "upsert_panel",
                        "panel_id": "combined-map",
                        "panel_type": "map_3d_google",
                        "title": "Verkeer en wind",
                        "bindings": [
                            {
                                "seed_handle_id": data.result["aliases"]["traffic"],
                                "field_bindings": {
                                    "latitude": "location.latitude",
                                    "longitude": "location.longitude",
                                },
                            },
                            {
                                "seed_handle_id": data.result["aliases"]["wind"],
                                "field_bindings": {
                                    "latitude": "location.latitude",
                                    "longitude": "location.longitude",
                                },
                            },
                        ],
                    }
                ],
            },
        ),
    )

    assert response.ok and isinstance(response.result, dict)
    panel = next(
        panel
        for panel in response.result["dashboard"]["panels"]
        if panel["panel_id"] == "combined-map"
    )
    assert panel["binding"]["kind"] == "events"
    assert [binding["kind"] for binding in panel["layer_bindings"]] == ["series"]
    assert len({panel["binding"]["binding_id"], panel["layer_bindings"][0]["binding_id"]}) == 2


async def test_dashboard_batch_accepts_multiple_event_feed_bindings(services):
    executor = await _executor(services)
    policy = executor.policy()
    data = await executor.execute(
        "data_batch",
        ToolRequest(
            request_id="multi-feed-data",
            session_policy_version=policy["version"],
            dashboard_version=1,
            payload={
                "operations": [
                    {
                        "operation": "query_events",
                        "stream": "ndw_incidents",
                        "limit": 12,
                        "save_as": "traffic",
                    },
                    {
                        "operation": "query_events",
                        "stream": "p2000",
                        "limit": 12,
                        "save_as": "dispatch",
                    },
                ]
            },
        ),
    )
    assert data.ok and isinstance(data.result, dict)

    response = await executor.execute(
        "dashboard_batch",
        ToolRequest(
            request_id="multi-feed-dashboard",
            session_policy_version=policy["version"],
            dashboard_version=1,
            payload={
                "expected_version": 1,
                "reason": "combineer verkeer en hulpverleningssignalen",
                "operations": [
                    {
                        "op": "upsert_panel",
                        "panel_id": "combined-feed",
                        "panel_type": "event_table",
                        "title": "Actuele meldingen",
                        "bindings": [
                            {
                                "seed_handle_id": data.result["aliases"]["traffic"],
                                "field_bindings": {
                                    "label": "title",
                                    "time": "observed_at",
                                },
                            },
                            {
                                "seed_handle_id": data.result["aliases"]["dispatch"],
                                "field_bindings": {
                                    "label": "title",
                                    "time": "observed_at",
                                },
                            },
                        ],
                    }
                ],
            },
        ),
    )

    assert response.ok and isinstance(response.result, dict)
    panel = next(
        item
        for item in response.result["dashboard"]["panels"]
        if item["panel_id"] == "combined-feed"
    )
    assert panel["binding"]["kind"] == "events"
    assert [binding["kind"] for binding in panel["layer_bindings"]] == ["events"]


async def test_dashboard_batch_rejects_multiple_correlation_bindings(services):
    executor = await _executor(services)
    operation = {
        "op": "upsert_panel",
        "panel_id": "invalid-correlation",
        "panel_type": "correlation",
        "title": "Ongeldige correlatie",
        "bindings": [{"seed_handle_id": "one"}, {"seed_handle_id": "two"}],
    }

    with pytest.raises(ToolExecutionError) as raised:
        executor._dashboard_operation(operation)
    assert raised.value.code == "MULTI_BINDING_UNSUPPORTED"


async def test_data_batch_accepts_at_prefixed_save_aliases(services):
    executor = await _executor(services)
    response = await executor.execute(
        "data_batch",
        ToolRequest(
            request_id="at-prefixed-alias",
            session_policy_version=executor.policy()["version"],
            payload={
                "operations": [
                    {
                        "operation": "query_measurements",
                        "stream": "knmi_observations",
                        "metric": "air_temperature_c",
                        "limit": 5,
                        "save_as": "@knmi_temp",
                    }
                ]
            },
        ),
    )

    assert response.ok
    assert isinstance(response.result, dict)
    assert "knmi_temp" in response.result["aliases"]


async def test_data_batch_resolves_bidirectional_cross_source_radius_aliases(services):
    executor = await _executor(services)
    response = await executor.execute(
        "data_batch",
        ToolRequest(
            request_id="cross-source-radius",
            session_policy_version=executor.policy()["version"],
            payload={
                "operations": [
                    {
                        "operation": "query_events",
                        "stream": "p2000",
                        "filters": [
                            {
                                "field": "record_id",
                                "op": "eq",
                                "value": "fixture-p2000-001",
                            }
                        ],
                        "limit": 1,
                        "save_as": "origin",
                    },
                    {
                        "operation": "query_nearby",
                        "stream": "rws_water",
                        "origin_handle": "@origin",
                        "radius_m": 10_000,
                        "limit": 10,
                        "save_as": "water_nearby",
                    },
                    {
                        "operation": "query_nearby",
                        "stream": "luchtmeetnet",
                        "origin_handle": "@origin",
                        "radius_m": 10_000,
                        "limit": 10,
                        "save_as": "air_nearby",
                    },
                ]
            },
        ),
    )

    assert response.ok and isinstance(response.result, dict)
    results = {item["alias"]: item for item in response.result["results"]}
    assert results["water_nearby"]["preview"][0]["distance_m"] < 200
    assert results["air_nearby"]["preview"][0]["distance_m"] < 10_000
    assert set(response.result["aliases"]) == {"origin", "water_nearby", "air_nearby"}


async def test_data_batch_filters_fixed_sources_around_geocoded_origin_text(services, monkeypatch):
    executor = await _executor(services)
    bundle = executor.query.latest_bundle()
    source_origin = executor.query.execute(
        {
            "operation": "query_events",
            "stream": "p2000",
            "filters": [{"field": "record_id", "op": "eq", "value": "fixture-p2000-001"}],
            "limit": 1,
        },
        bundle,
    )
    _handle, origin_rows = executor.query.load(source_origin.handle_id)
    origin_location = origin_rows[0]["location"]
    monkeypatch.setattr(
        executor.geocoding,
        "resolve",
        AsyncMock(
            return_value={
                "matches": [
                    {
                        "place_id": "fixture-place",
                        "display_label": "Varsseveld, Nederland",
                        "location": {
                            "lat": origin_location["latitude"],
                            "lng": origin_location["longitude"],
                        },
                    }
                ]
            }
        ),
    )

    response = await executor.execute(
        "data_batch",
        ToolRequest(
            request_id="geocoded-cross-source-radius",
            session_policy_version=executor.policy()["version"],
            payload={
                "operations": [
                    {
                        "operation": "query_nearby",
                        "stream": "rws_water",
                        "origin_text": "Varsseveld",
                        "radius_m": 10_000,
                        "limit": 10,
                        "save_as": "water_nearby",
                    }
                ]
            },
        ),
    )

    assert response.ok and isinstance(response.result, dict)
    result = response.result["results"][0]
    assert result["row_count"] == 1
    assert all(row["distance_m"] <= 10_000 for row in result["preview"])
    assert all(row["distance_origin_record_id"].startswith("locres_") for row in result["preview"])


async def test_dashboard_batch_uses_top_level_handle_when_model_emits_empty_binding(
    services,
):
    executor = await _executor(services)
    policy = executor.policy()
    data = await executor.execute(
        "data_batch",
        ToolRequest(
            request_id="empty-binding-data",
            session_policy_version=policy["version"],
            dashboard_version=1,
            payload={
                "operations": [
                    {
                        "operation": "query_events",
                        "stream": "p2000",
                        "window": "PT60M",
                        "limit": 10,
                        "save_as": "signals",
                    }
                ]
            },
        ),
    )
    assert data.ok and isinstance(data.result, dict)

    dashboard = await executor.execute(
        "dashboard_batch",
        ToolRequest(
            request_id="empty-binding-dashboard",
            session_policy_version=policy["version"],
            dashboard_version=1,
            payload={
                "expected_version": 1,
                "composition_mode": "replace_visible",
                "reason": "toon signalen",
                "operations": [
                    {
                        "op": "upsert_panel",
                        "panel_id": "signals",
                        "panel_type": "event_table",
                        "title": "P2000-signalen",
                        "seed_handle_id": data.result["aliases"]["signals"],
                        "binding": {},
                    }
                ],
            },
        ),
    )

    assert dashboard.ok and isinstance(dashboard.result, dict)
    panel = next(
        item for item in dashboard.result["dashboard"]["panels"] if item["panel_id"] == "signals"
    )
    binding = panel["binding"]
    assert binding is not None
    assert binding["kind"] == "events"


async def test_rws_snapshot_reports_no_timeseries_and_rejects_line_panel(services):
    executor = await _executor(services)
    policy = executor.policy()
    data = await executor.execute(
        "data_batch",
        ToolRequest(
            request_id="rws-current-snapshot",
            session_policy_version=policy["version"],
            payload={
                "operations": [
                    {
                        "operation": "query_measurements",
                        "stream": "rws_water",
                        "metric": "water_level_cm",
                        "window": "P14D",
                        "limit": 100,
                        "save_as": "water",
                    }
                ]
            },
        ),
    )

    assert data.ok and isinstance(data.result, dict)
    result = data.result["results"][0]
    assert result["series_profile"] == {
        "distinct_timestamps": 1,
        "series_count": 1,
        "series_with_history": 0,
        "max_points_per_series": 1,
        "supports_timeseries": False,
        "requested_window": "P14D",
        "history_mode": "current_snapshot",
        "recommended_panel": "ranking_or_map",
    }

    dashboard = await executor.execute(
        "dashboard_batch",
        ToolRequest(
            request_id="reject-rws-single-point-line",
            session_policy_version=policy["version"],
            dashboard_version=1,
            payload={
                "expected_version": 1,
                "operations": [
                    {
                        "op": "upsert_panel",
                        "panel_id": "rws-history",
                        "panel_type": "timeseries",
                        "title": "Waterpeil veertien dagen",
                        "seed_handle_id": data.result["aliases"]["water"],
                        "binding": {
                            "seed_handle_id": data.result["aliases"]["water"],
                            "field_bindings": {"x": "observed_at", "y": "value"},
                        },
                    }
                ],
            },
        ),
    )
    assert not dashboard.ok
    assert dashboard.error and dashboard.error["code"] == "INSUFFICIENT_SERIES"
    assert dashboard.error["details"]["available_timestamps"] == 1


async def test_data_batch_runs_independent_queries_in_parallel(services, monkeypatch):
    executor = await _executor(services)
    original_prepare = executor.query.prepare
    lock = threading.Lock()
    active = 0
    max_active = 0

    def delayed_prepare(query_spec, bundle_version=None):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.05)
            return original_prepare(query_spec, bundle_version)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(executor.query, "prepare", delayed_prepare)
    response = await executor.execute(
        "data_batch",
        ToolRequest(
            request_id="parallel-independent-queries",
            session_policy_version=executor.policy()["version"],
            payload={
                "operations": [
                    {
                        "operation": "query_events",
                        "stream": "p2000",
                        "limit": 5,
                        "save_as": "incidents",
                    },
                    {
                        "operation": "query_measurements",
                        "stream": "knmi_observations",
                        "metric": "wind_gust_kmh",
                        "limit": 5,
                        "save_as": "wind",
                    },
                ]
            },
        ),
    )

    assert response.ok
    assert max_active >= 2
    assert isinstance(response.result, dict)
    assert list(response.result["aliases"]) == ["incidents", "wind"]


async def test_data_batch_resolves_dependent_aliases_after_parallel_queries(services):
    executor = await _executor(services)
    response = await executor.execute(
        "data_batch",
        ToolRequest(
            request_id="parallel-alias-barrier",
            session_policy_version=executor.policy()["version"],
            payload={
                "operations": [
                    {
                        "operation": "query_measurements",
                        "stream": "knmi_observations",
                        "metric": "wind_gust_kmh",
                        "limit": 5,
                        "save_as": "wind",
                    },
                    {
                        "operation": "answer_slice",
                        "input_handle": "@wind",
                        "limit": 2,
                        "save_as": "wind_excerpt",
                    },
                ]
            },
        ),
    )

    assert response.ok
    assert isinstance(response.result, dict)
    assert list(response.result["aliases"]) == ["wind", "wind_excerpt"]
    assert response.result["results"][1]["row_count"] <= 2


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


async def test_correlation_panel_rejects_non_correlation_handle(services):
    executor = await _executor(services)
    policy = executor.policy()
    data = await executor.execute(
        "data_batch",
        ToolRequest(
            request_id="wind-series-for-invalid-correlation",
            session_policy_version=policy["version"],
            payload={
                "operations": [
                    {
                        "operation": "query_measurements",
                        "stream": "knmi_observations",
                        "metric": "wind_gust_kmh",
                        "limit": 10,
                        "save_as": "wind",
                    }
                ]
            },
        ),
    )
    assert data.ok and isinstance(data.result, dict)
    response = await executor.execute(
        "dashboard_batch",
        ToolRequest(
            request_id="reject-fake-correlation",
            session_policy_version=policy["version"],
            dashboard_version=1,
            payload={
                "expected_version": 1,
                "reason": "mag geen schijncorrelatie tonen",
                "operations": [
                    {
                        "op": "upsert_panel",
                        "panel_id": "invalid-correlation",
                        "panel_type": "correlation",
                        "title": "Ongeldige correlatie",
                        "seed_handle_id": data.result["aliases"]["wind"],
                    }
                ],
            },
        ),
    )
    assert not response.ok
    assert response.error and response.error["code"] == "INCOMPATIBLE_PANEL_BINDING"


async def test_dashboard_adaptive_composition_keeps_twelve_visible_panels(services):
    executor = await _executor(services)
    data = await executor.execute(
        "data_batch",
        ToolRequest(
            request_id="bounded-layout-data",
            session_policy_version=executor.policy()["version"],
            payload={
                "operations": [
                    {
                        "operation": "query_events",
                        "stream": "p2000",
                        "limit": 10,
                        "save_as": "events",
                    }
                ]
            },
        ),
    )
    assert data.ok and isinstance(data.result, dict)
    handle_id = data.result["aliases"]["events"]

    first = await executor.execute(
        "dashboard_batch",
        ToolRequest(
            request_id="bounded-layout-twelve",
            session_policy_version=executor.policy()["version"],
            dashboard_version=1,
            payload={
                "expected_version": 1,
                "composition_mode": "replace_visible",
                "reason": "maak twaalf panelen",
                "operations": [
                    {
                        "op": "upsert_panel",
                        "panel_id": f"bounded-{index}",
                        "panel_type": "event_table",
                        "title": f"Meldingen {index}",
                        "seed_handle_id": handle_id,
                        "binding": {
                            "seed_handle_id": handle_id,
                            "field_bindings": {"label": "title", "time": "observed_at"},
                        },
                        "order": index,
                    }
                    for index in range(12)
                ],
            },
        ),
    )
    assert first.ok
    assert isinstance(first.result, dict)
    first_visible = [
        panel
        for panel in first.result["dashboard"]["panels"]
        if panel["panel_type"] not in {"source_health", "evidence"}
    ]
    assert len(first_visible) == 12

    second = await executor.execute(
        "dashboard_batch",
        ToolRequest(
            request_id="bounded-layout-adaptive",
            session_policy_version=executor.policy()["version"],
            dashboard_version=2,
            payload={
                "expected_version": 2,
                "composition_mode": "adaptive",
                "reason": "voeg een actuele ranglijst toe",
                "operations": [
                    {
                        "op": "upsert_panel",
                        "panel_id": "bounded-new",
                        "panel_type": "event_table",
                        "title": "Nieuwe meldingen",
                        "seed_handle_id": handle_id,
                        "binding": {
                            "seed_handle_id": handle_id,
                            "field_bindings": {"label": "title", "time": "observed_at"},
                        },
                        "order": 1,
                    }
                ],
            },
        ),
    )
    assert second.ok and isinstance(second.result, dict)
    visible = [
        panel
        for panel in second.result["dashboard"]["panels"]
        if panel["panel_type"] not in {"source_health", "evidence"}
    ]
    assert len(visible) == 12
    assert "bounded-new" in {panel["panel_id"] for panel in visible}
    assert second.result["auto_removed_panel_ids"] == ["bounded-11"]

    thirteenth = await executor.execute(
        "dashboard_batch",
        ToolRequest(
            request_id="bounded-layout-thirteenth",
            session_policy_version=executor.policy()["version"],
            dashboard_version=3,
            payload={
                "expected_version": 3,
                "composition_mode": "merge",
                "reason": "probeer een dertiende paneel toe te voegen",
                "operations": [
                    {
                        "op": "upsert_panel",
                        "panel_id": "bounded-thirteenth",
                        "panel_type": "event_table",
                        "title": "Dertiende paneel",
                        "seed_handle_id": handle_id,
                        "binding": {
                            "seed_handle_id": handle_id,
                            "field_bindings": {"label": "title", "time": "observed_at"},
                        },
                        "order": 13,
                    }
                ],
            },
        ),
    )
    assert not thirteenth.ok
    assert thirteenth.error and thirteenth.error["code"] == "PANEL_LIMIT_EXCEEDED"
    assert thirteenth.error["details"] == {
        "maximum": 12,
        "projected": 13,
        "hint": "Gebruik replace_visible of verwijder bestaande panelen in dezelfde batch.",
    }


async def test_dashboard_replace_visible_removes_previous_workspace(services):
    executor = await _executor(services)
    data = await executor.execute(
        "data_batch",
        ToolRequest(
            request_id="replace-layout-data",
            session_policy_version=executor.policy()["version"],
            payload={
                "operations": [
                    {
                        "operation": "query_events",
                        "stream": "p2000",
                        "limit": 10,
                        "save_as": "events",
                    }
                ]
            },
        ),
    )
    assert data.ok and isinstance(data.result, dict)
    result = await executor.execute(
        "dashboard_batch",
        ToolRequest(
            request_id="replace-layout",
            session_policy_version=executor.policy()["version"],
            dashboard_version=1,
            payload={
                "expected_version": 1,
                "composition_mode": "replace_visible",
                "reason": "alleen recente meldingen",
                "operations": [
                    {
                        "op": "upsert_panel",
                        "panel_id": "only-events",
                        "panel_type": "incident_timeline",
                        "title": "Recente meldingen",
                        "seed_handle_id": data.result["aliases"]["events"],
                        "binding": {
                            "seed_handle_id": data.result["aliases"]["events"],
                            "field_bindings": {"time": "observed_at", "label": "title"},
                        },
                    }
                ],
            },
        ),
    )
    assert result.ok and isinstance(result.result, dict)
    visible_ids = {
        panel["panel_id"]
        for panel in result.result["dashboard"]["panels"]
        if panel["panel_type"] not in {"source_health", "evidence"}
    }
    assert visible_ids == {"only-events"}
