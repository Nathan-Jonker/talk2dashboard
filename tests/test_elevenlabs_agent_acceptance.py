from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from talk2dashboard.tools.executor import ToolExecutor

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts/test_elevenlabs_agent_routing.py"
SPEC = importlib.util.spec_from_file_location("elevenlabs_agent_acceptance", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

AGENT_CASES = MODULE.AGENT_CASES
AgentCase = MODULE.AgentCase
case_fingerprint = MODULE.case_fingerprint
select_cases = MODULE.select_cases
validate = MODULE.validate


def _run(tool: str, params: dict) -> dict:
    return {
        "agent_responses": [
            {
                "tool_calls": [
                    {
                        "tool_name": tool,
                        "params_as_json": json.dumps(params),
                    }
                ]
            }
        ]
    }


def test_agent_acceptance_suite_covers_forty_two_cases_all_sources_and_tools() -> None:
    assert len(AGENT_CASES) == 42
    assert len({case.name for case in AGENT_CASES}) == 42
    assert {case.expected_tool for case in AGENT_CASES} == {
        "inspect_workspace",
        "data_batch",
        "dashboard_batch",
        "nearby_places",
        "capture_dashboard",
        "external_search",
    }
    covered_streams = {
        expectation.stream for case in AGENT_CASES for expectation in case.operations
    }
    assert covered_streams == {
        "knmi_observations",
        "rws_water",
        "luchtmeetnet",
        "ndw_incidents",
        "p2000",
        "ns_disruptions",
        "nos_rss",
    }


def test_cross_source_radius_cases_require_alias_based_nearby_queries() -> None:
    for name in ("p2000-cross-source-radius", "knmi-cross-source-radius"):
        case = next(case for case in AGENT_CASES if case.name == name)
        operations = [
            {
                "operation": expectation.operation,
                "stream": expectation.stream,
                "metric": expectation.metric,
                "save_as": "origin" if index == 0 else f"nearby_{index}",
                **(
                    {"origin_handle": "@origin", "radius_m": 10_000}
                    if expectation.operation == "query_nearby"
                    else {}
                ),
            }
            for index, expectation in enumerate(case.operations)
        ]
        ok, detail = validate(case, _run("data_batch", {"operations": operations}))
        assert ok, detail


def test_validator_checks_multisource_operations_and_metrics() -> None:
    case = next(case for case in AGENT_CASES if case.name == "environment-batch")
    ok, _ = validate(
        case,
        _run(
            "data_batch",
            {
                "operations": [
                    {
                        "operation": "query_measurements",
                        "stream": "knmi_observations",
                        "metric": "air_temperature_c",
                    },
                    {
                        "operation": "query_measurements",
                        "stream": "rws_water",
                        "metric": "water_level_cm",
                    },
                    {
                        "operation": "query_measurements",
                        "stream": "luchtmeetnet",
                        "metric": "no2_ug_m3",
                    },
                ]
            },
        ),
    )
    assert ok

    invalid, detail = validate(
        case,
        _run(
            "data_batch",
            {
                "operations": [
                    {
                        "operation": "query_measurements",
                        "stream": "knmi_observations",
                        "metric": "wind_gust_kmh",
                    }
                ]
            },
        ),
    )
    assert not invalid
    assert "air_temperature_c" in detail


def test_validator_requires_correlation_after_both_air_quality_queries() -> None:
    case = next(case for case in AGENT_CASES if case.name == "lucht-no2-pm25-correlation")
    params = {
        "operations": [
            {
                "operation": "query_measurements",
                "stream": "luchtmeetnet",
                "metric": "no2_ug_m3",
                "save_as": "no2",
            },
            {
                "operation": "query_measurements",
                "stream": "luchtmeetnet",
                "metric": "pm25_ug_m3",
                "save_as": "pm25",
            },
            {
                "operation": "correlate",
                "series_a": "@no2",
                "series_b": "@pm25",
            },
        ]
    }
    ok, _ = validate(case, _run("data_batch", params))
    assert ok

    invalid, detail = validate(case, _run("data_batch", {"operations": params["operations"][:2]}))
    assert not invalid
    assert "correlate" in detail


def test_validator_accepts_one_query_with_multiple_metrics() -> None:
    case = next(case for case in AGENT_CASES if case.name == "weather-hazard-batch")
    ok, _ = validate(
        case,
        _run(
            "data_batch",
            {
                "operations": [
                    {
                        "operation": "query_measurements",
                        "stream": "knmi_observations",
                        "metrics": ["wind_gust_kmh", "rainfall_rate_mm_h"],
                    }
                ]
            },
        ),
    )
    assert ok


def test_validator_checks_nearby_contract_and_forbidden_fallback() -> None:
    case = next(case for case in AGENT_CASES if case.name == "nearby-healthcare-education")
    ok, _ = validate(
        case,
        _run(
            "nearby_places",
            {
                "origin_text": "IJmuiden",
                "included_types": ["school", "hospital"],
                "radius_m": 5000,
                "rank": "distance",
            },
        ),
    )
    assert ok

    invalid, detail = validate(
        case,
        {
            "agent_responses": [
                {
                    "tool_calls": [
                        {
                            "tool_name": "nearby_places",
                            "params_as_json": json.dumps(
                                {
                                    "origin_text": "IJmuiden",
                                    "included_types": ["hospital"],
                                    "radius_m": 5000,
                                    "rank": "distance",
                                }
                            ),
                        },
                        {"tool_name": "external_search", "params_as_json": "{}"},
                    ]
                }
            ]
        },
    )
    assert not invalid
    assert "verboden toolcall" in detail


def test_validator_accepts_map_mode_inside_dashboard_operation_values() -> None:
    case = next(case for case in AGENT_CASES if case.name == "dashboard-map-3d")
    ok, _ = validate(
        case,
        _run(
            "dashboard_batch",
            {
                "operations": [
                    {
                        "op": "set_map_mode",
                        "values": {
                            "panel_id": "startup-ndw-incidents",
                            "panel_type": "map_3d_google",
                        },
                    }
                ],
                "reason": "Maak de bestaande kaart driedimensionaal.",
            },
        ),
    )
    assert ok


def test_validator_and_executor_accept_flat_map_mode_fields() -> None:
    case = next(case for case in AGENT_CASES if case.name == "dashboard-map-3d")
    payload = {
        "operations": [
            {
                "op": "set_map_mode",
                "panel_id": "startup-ndw-incidents",
                "panel_type": "map_3d_google",
            }
        ],
        "reason": "Maak de bestaande kaart driedimensionaal.",
    }
    ok, _ = validate(case, _run("dashboard_batch", payload))
    assert ok

    executor = object.__new__(ToolExecutor)
    operation = executor._dashboard_operation(payload["operations"][0])
    assert operation.values == {
        "panel_id": "startup-ndw-incidents",
        "panel_type": "map_3d_google",
    }


def test_validator_and_executor_normalize_layout_template_inside_set_meta() -> None:
    case = next(case for case in AGENT_CASES if case.name == "dashboard-mobility-layout")
    payload = {
        "operations": [
            {
                "op": "set_meta",
                "values": {"layout_template": "mobility_focus"},
            }
        ]
    }
    ok, _ = validate(case, _run("dashboard_batch", payload))
    assert ok

    executor = object.__new__(ToolExecutor)
    operation = executor._dashboard_operation(payload["operations"][0])
    assert operation.op == "set_layout_template"
    assert operation.values == {"layout_template": "mobility_focus"}


def test_validator_accepts_default_2d_map_mode() -> None:
    case = next(case for case in AGENT_CASES if case.name == "dashboard-map-2d")
    ok, _ = validate(
        case,
        _run(
            "dashboard_batch",
            {"operations": [{"op": "set_map_mode", "values": {}}]},
        ),
    )
    assert ok


def test_default_selection_skips_unchanged_passes_and_keeps_failures() -> None:
    passed = AGENT_CASES[0]
    failed = AGENT_CASES[1]
    previous = {
        "cases": {
            passed.name: {
                "status": "pass",
                "fingerprint": case_fingerprint(passed),
            },
            failed.name: {
                "status": "fail",
                "fingerprint": case_fingerprint(failed),
            },
        }
    }
    selected = select_cases(previous, force_all=False, requested=set())
    assert passed not in selected
    assert failed in selected
    assert len(selected) == len(AGENT_CASES) - 1


def test_explicit_case_selection_runs_even_when_it_previously_passed() -> None:
    case: AgentCase = AGENT_CASES[0]
    previous = {
        "cases": {
            case.name: {
                "status": "pass",
                "fingerprint": case_fingerprint(case),
            }
        }
    }
    assert select_cases(previous, force_all=False, requested={case.name}) == [case]
