from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from talk2dashboard.config import get_settings  # noqa: E402

API_ROOT = "https://api.elevenlabs.io"
VALIDATOR_VERSION = 2
DEFAULT_RESULTS = PROJECT_ROOT / "artifacts" / "elevenlabs-agent-acceptance.json"


@dataclass(frozen=True)
class OperationExpectation:
    stream: str
    operation: str
    metric: str | None = None
    sort: str | None = None
    order: str | None = None
    limit_max: int | None = None


@dataclass(frozen=True)
class AgentCase:
    name: str
    prompt: str
    expected_tool: str
    operations: tuple[OperationExpectation, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    expected_values: tuple[tuple[str, Any], ...] = ()
    expected_sets: tuple[tuple[str, tuple[str, ...]], ...] = ()
    query_terms: tuple[str, ...] = ()


AGENT_CASES = (
    AgentCase(
        "knmi-wind-ranking",
        "Maak een ranglijst van de tien hoogste actuele windstoten en onderbouw die met de bronstatus.",
        "data_batch",
        operations=(
            OperationExpectation(
                "knmi_observations",
                "query_measurements",
                "wind_gust_kmh",
                "value",
                "desc",
                10,
            ),
        ),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "knmi-temperature-map",
        "Toon de actuele KNMI-temperaturen per meetstation op een kaart.",
        "data_batch",
        operations=(
            OperationExpectation("knmi_observations", "query_measurements", "air_temperature_c"),
        ),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "rws-water-ranking",
        "Toon de twaalf hoogste actuele waterstanden van Rijkswaterstaat als ranglijst.",
        "data_batch",
        operations=(
            OperationExpectation(
                "rws_water", "query_measurements", "water_level_cm", "value", "desc", 12
            ),
        ),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "lucht-no2-ranking",
        "Maak een ranglijst van de actuele stikstofdioxidewaarden van Luchtmeetnet.",
        "data_batch",
        operations=(
            OperationExpectation(
                "luchtmeetnet", "query_measurements", "no2_ug_m3", "value", "desc"
            ),
        ),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "lucht-pm25-series",
        "Toon het recente verloop van fijnstof PM2,5 van Luchtmeetnet en gebruik alleen een tijdgrafiek als er echte historie is.",
        "data_batch",
        operations=(
            OperationExpectation(
                "luchtmeetnet", "query_measurements", "pm25_ug_m3", "observed_at", "asc"
            ),
        ),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "ndw-incidents",
        "Toon de actuele wegincidenten en afsluitingen van NDW.",
        "data_batch",
        operations=(OperationExpectation("ndw_incidents", "query_events"),),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "p2000-signals",
        "Toon de nieuwste P2000-signalen als meldingenlijst en behandel ze niet als bevestigde incidenten.",
        "data_batch",
        operations=(OperationExpectation("p2000", "query_events"),),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "ns-disruptions",
        "Toon de actuele spoorstoringen en werkzaamheden van NS.",
        "data_batch",
        operations=(OperationExpectation("ns_disruptions", "query_events"),),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "nos-context",
        "Toon de nieuwste NOS-berichten als afzonderlijke nieuwscontext.",
        "data_batch",
        operations=(OperationExpectation("nos_rss", "query_events"),),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "environment-batch",
        "Vergelijk in een keer actuele temperatuur, waterstand en stikstofdioxide uit KNMI, Rijkswaterstaat en Luchtmeetnet.",
        "data_batch",
        operations=(
            OperationExpectation("knmi_observations", "query_measurements", "air_temperature_c"),
            OperationExpectation("rws_water", "query_measurements", "water_level_cm"),
            OperationExpectation("luchtmeetnet", "query_measurements", "no2_ug_m3"),
        ),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "incident-batch",
        "Bouw een actueel incidentbeeld met P2000-signalen en NDW-verkeersincidenten.",
        "data_batch",
        operations=(
            OperationExpectation("p2000", "query_events"),
            OperationExpectation("ndw_incidents", "query_events"),
        ),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "p2000-cross-source-radius",
        "Neem de nieuwste P2000-melding als middelpunt en toon in dezelfde 3D-kaart alle NDW-meldingen, watermeetpunten, luchtmeetpunten, KNMI-metingen en NS-storingen binnen tien kilometer.",
        "data_batch",
        operations=(
            OperationExpectation("p2000", "query_events"),
            OperationExpectation("ndw_incidents", "query_nearby"),
            OperationExpectation("rws_water", "query_nearby"),
            OperationExpectation("luchtmeetnet", "query_nearby"),
            OperationExpectation("knmi_observations", "query_nearby"),
            OperationExpectation("ns_disruptions", "query_nearby"),
        ),
        forbidden_tools=("inspect_workspace", "nearby_places"),
    ),
    AgentCase(
        "knmi-cross-source-radius",
        "Neem de hoogste actuele KNMI-windstoot als middelpunt en zoek binnen tien kilometer de dichtstbijzijnde waterstanden, luchtmetingen, P2000-signalen, wegmeldingen en NS-storingen.",
        "data_batch",
        operations=(
            OperationExpectation("knmi_observations", "query_measurements", "wind_gust_kmh"),
            OperationExpectation("rws_water", "query_nearby"),
            OperationExpectation("luchtmeetnet", "query_nearby"),
            OperationExpectation("p2000", "query_nearby"),
            OperationExpectation("ndw_incidents", "query_nearby"),
            OperationExpectation("ns_disruptions", "query_nearby"),
        ),
        forbidden_tools=("inspect_workspace", "nearby_places"),
    ),
    AgentCase(
        "road-rail-batch",
        "Vergelijk actuele wegincidenten met spoorstoringen, zonder te beweren dat het ene het andere veroorzaakt.",
        "data_batch",
        operations=(
            OperationExpectation("ndw_incidents", "query_events"),
            OperationExpectation("ns_disruptions", "query_events"),
        ),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "wind-incident-causality",
        "Hebben de actuele windstoten geleid tot meer wegongevallen? Controleer eerst of geldige gekoppelde historische data beschikbaar is.",
        "data_batch",
        operations=(
            OperationExpectation("knmi_observations", "query_measurements", "wind_gust_kmh"),
            OperationExpectation("ndw_incidents", "query_events"),
        ),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "inspect-capabilities",
        "Welke databronnen en paneltypen zijn er beschikbaar?",
        "inspect_workspace",
        expected_sets=(("sections", ("streams", "panel_types")),),
    ),
    AgentCase(
        "nearby-healthcare-education",
        "Welke ziekenhuizen en scholen liggen vanuit IJmuiden binnen vijf kilometer?",
        "nearby_places",
        forbidden_tools=("external_search",),
        expected_values=(("origin_text", "IJmuiden"), ("radius_m", 5000), ("rank", "distance")),
        expected_sets=(("included_types", ("hospital", "school")),),
    ),
    AgentCase(
        "external-search",
        "Websearch staat aan. Zoek maximaal vijf actuele publieke resultaten over de A16 bij Moerdijk.",
        "external_search",
        expected_values=(("max_results", 5),),
        query_terms=("a16", "moerdijk"),
    ),
    AgentCase(
        "dashboard-title",
        "Wijzig alleen de dashboardtitel in 'Landelijk operationeel beeld'.",
        "dashboard_batch",
        expected_values=(
            ("operations.0.op", "set_meta"),
            ("operations.0.values.title", "Landelijk operationeel beeld"),
        ),
    ),
    AgentCase(
        "dashboard-map-3d",
        "Maak de bestaande kaart driedimensionaal met Google 3D.",
        "dashboard_batch",
        expected_values=(
            ("operations.0.op", "set_map_mode"),
            ("operations.0.values.panel_type", "map_3d_google"),
        ),
    ),
    AgentCase(
        "dashboard-undo",
        "Maak de laatste dashboardwijziging ongedaan.",
        "dashboard_batch",
        expected_values=(("operations.0.op", "undo"),),
    ),
    AgentCase(
        "capture-full-dashboard",
        "Maak een screenshot en gestructureerde snapshot van het volledige dashboard.",
        "capture_dashboard",
        expected_values=(
            ("scope", "full_dashboard"),
            ("include_structured_state", True),
        ),
    ),
    AgentCase(
        "knmi-rain-ranking",
        "Rangschik de vijf KNMI-stations met de hoogste actuele neerslagintensiteit.",
        "data_batch",
        operations=(
            OperationExpectation(
                "knmi_observations",
                "query_measurements",
                "rainfall_rate_mm_h",
                "value",
                "desc",
                5,
            ),
        ),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "knmi-wind-speed-ranking",
        "Geef de acht KNMI-stations met de hoogste actuele windsnelheid, niet de windstoot.",
        "data_batch",
        operations=(
            OperationExpectation(
                "knmi_observations",
                "query_measurements",
                "wind_speed_ms",
                "value",
                "desc",
                8,
            ),
        ),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "rws-water-lowest",
        "Toon de zes laagste actuele waterstanden van Rijkswaterstaat als ranglijst.",
        "data_batch",
        operations=(
            OperationExpectation(
                "rws_water", "query_measurements", "water_level_cm", "value", "asc", 6
            ),
        ),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "lucht-ozone-ranking",
        "Maak een top tien van de hoogste actuele ozonwaarden van Luchtmeetnet.",
        "data_batch",
        operations=(
            OperationExpectation(
                "luchtmeetnet", "query_measurements", "o3_ug_m3", "value", "desc", 10
            ),
        ),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "lucht-pm10-ranking",
        "Rangschik de actuele PM10-fijnstofwaarden van Luchtmeetnet van hoog naar laag.",
        "data_batch",
        operations=(
            OperationExpectation(
                "luchtmeetnet", "query_measurements", "pm10_ug_m3", "value", "desc"
            ),
        ),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "weather-hazard-batch",
        "Controleer in een keer de actuele windstoten en neerslagintensiteit van KNMI.",
        "data_batch",
        operations=(
            OperationExpectation("knmi_observations", "query_measurements", "wind_gust_kmh"),
            OperationExpectation("knmi_observations", "query_measurements", "rainfall_rate_mm_h"),
        ),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "air-quality-four-metrics",
        "Vergelijk actueel NO2, ozon, PM10 en PM2,5 van Luchtmeetnet in een enkele dataopvraag.",
        "data_batch",
        operations=(
            OperationExpectation("luchtmeetnet", "query_measurements", "no2_ug_m3"),
            OperationExpectation("luchtmeetnet", "query_measurements", "o3_ug_m3"),
            OperationExpectation("luchtmeetnet", "query_measurements", "pm10_ug_m3"),
            OperationExpectation("luchtmeetnet", "query_measurements", "pm25_ug_m3"),
        ),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "national-event-context-four-streams",
        "Maak één actueel overzicht met NDW-incidenten, P2000-signalen, NS-storingen en NOS-nieuwscontext.",
        "data_batch",
        operations=(
            OperationExpectation("ndw_incidents", "query_events"),
            OperationExpectation("p2000", "query_events"),
            OperationExpectation("ns_disruptions", "query_events"),
            OperationExpectation("nos_rss", "query_events"),
        ),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "ndw-category-count",
        "Tel de actuele NDW-meldingen per categorie zodat ik categorieën kan vergelijken.",
        "data_batch",
        operations=(OperationExpectation("ndw_incidents", "aggregate"),),
        expected_values=(
            ("operations.0.group_by", "category"),
            ("operations.0.fn", "count"),
        ),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "knmi-wind-baseline",
        "Vergelijk de huidige KNMI-windstoten met het historische normale niveau en meld het eerlijk als de baseline onvoldoende is.",
        "data_batch",
        operations=(OperationExpectation("knmi_observations", "baseline", "wind_gust_kmh"),),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "lucht-no2-pm25-correlation",
        "Onderzoek of NO2 en PM2,5 van Luchtmeetnet statistisch samenhangen; gebruik een echte correlatiebewerking en claim geen causaliteit.",
        "data_batch",
        operations=(OperationExpectation("luchtmeetnet", "correlate"),),
        forbidden_tools=("inspect_workspace",),
    ),
    AgentCase(
        "inspect-source-status",
        "Welke bekende databronnen zijn beschikbaar en wat is hun actuele status?",
        "inspect_workspace",
        expected_sets=(("sections", ("streams",)),),
        expected_values=(("detail", "compact"),),
    ),
    AgentCase(
        "inspect-dashboard-policies",
        "Inspecteer compact de huidige dashboardconfiguratie en de geldende veiligheidsregels.",
        "inspect_workspace",
        expected_sets=(("sections", ("dashboard", "policies")),),
        expected_values=(("detail", "compact"),),
    ),
    AgentCase(
        "nearby-emergency-rotterdam",
        "Welke ziekenhuizen, politiebureaus en brandweerkazernes liggen binnen vier kilometer van Rotterdam Centraal?",
        "nearby_places",
        forbidden_tools=("external_search",),
        expected_values=(
            ("origin_text", "Rotterdam Centraal"),
            ("radius_m", 4000),
            ("rank", "distance"),
        ),
        expected_sets=(("included_types", ("hospital", "police", "fire_station")),),
    ),
    AgentCase(
        "nearby-transit-pharmacy-utrecht",
        "Zoek binnen twee kilometer van Utrecht Centraal apotheken, trein- en busstations, gesorteerd op afstand.",
        "nearby_places",
        forbidden_tools=("external_search",),
        expected_values=(
            ("origin_text", "Utrecht Centraal"),
            ("radius_m", 2000),
            ("rank", "distance"),
        ),
        expected_sets=(("included_types", ("pharmacy", "train_station", "bus_station")),),
    ),
    AgentCase(
        "external-search-almere-news",
        "Websearch staat aan. Zoek maximaal vijf actuele publieke nieuwsresultaten over Almere van de afgelopen twee dagen.",
        "external_search",
        expected_values=(("max_results", 5), ("recency_days", 2)),
        query_terms=("almere",),
    ),
    AgentCase(
        "dashboard-mobility-layout",
        "Zet alleen de dashboardindeling om naar de mobiliteitsfocus.",
        "dashboard_batch",
        expected_values=(
            ("operations.0.op", "set_layout_template"),
            ("operations.0.values.layout_template", "mobility_focus"),
        ),
    ),
    AgentCase(
        "dashboard-focus-ijmuiden",
        "Verplaats alleen de dashboardfocus naar IJmuiden.",
        "dashboard_batch",
        expected_values=(
            ("operations.0.op", "set_focus"),
            ("operations.0.values.map_focus", "IJmuiden"),
        ),
    ),
    AgentCase(
        "dashboard-map-2d",
        "Maak de bestaande incidentkaart expliciet plat en tweedimensionaal.",
        "dashboard_batch",
        expected_values=(
            ("operations.0.op", "set_map_mode"),
            ("operations.0.values.panel_type", "map_2d"),
        ),
    ),
    AgentCase(
        "capture-incident-panel",
        "Leg alleen paneel startup-ndw-incidents vast, inclusief structured state en visuele analyse.",
        "capture_dashboard",
        expected_values=(
            ("scope", "panel"),
            ("panel_id", "startup-ndw-incidents"),
            ("include_structured_state", True),
            ("analyze", True),
        ),
    ),
)


def safe_error(response: httpx.Response) -> str:
    try:
        value = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    return json.dumps(value, ensure_ascii=True)[:1600]


def request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    response = client.request(method, f"{API_ROOT}{path}", **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path}: {safe_error(response)}")
    return response.json() if response.content else None


def tool_calls(test_run: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for response in test_run.get("agent_responses", []):
        calls.extend(response.get("tool_calls") or [])
    return calls


def parse_params(call: dict[str, Any]) -> dict[str, Any]:
    raw = call.get("params_as_json") or "{}"
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def nested_value(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def expected_value(value: dict[str, Any], path: str) -> Any:
    actual = nested_value(value, path)
    if actual is not None:
        return actual
    if ".values." in path:
        return nested_value(value, path.replace(".values.", "."))
    return None


def operation_streams(operation: dict[str, Any]) -> set[str]:
    streams = operation.get("streams")
    result = set(str(item) for item in streams) if isinstance(streams, list) else set()
    if operation.get("stream"):
        result.add(str(operation["stream"]))
    return result


def operation_metrics(operation: dict[str, Any]) -> set[str]:
    metrics = operation.get("metrics")
    result = set(str(item) for item in metrics) if isinstance(metrics, list) else set()
    if operation.get("metric"):
        result.add(str(operation["metric"]))
    return result


def matching_operation(
    operations: list[dict[str, Any]], expectation: OperationExpectation
) -> dict[str, Any] | None:
    direct = next(
        (
            operation
            for operation in operations
            if expectation.stream in operation_streams(operation)
            and operation.get("operation") == expectation.operation
            and (expectation.metric is None or expectation.metric in operation_metrics(operation))
        ),
        None,
    )
    if direct is not None or expectation.operation != "correlate":
        return direct
    queried_streams = set().union(*(operation_streams(operation) for operation in operations))
    if expectation.stream not in queried_streams:
        return None
    return next(
        (operation for operation in operations if operation.get("operation") == "correlate"),
        None,
    )


def validate(case: AgentCase, test_run: dict[str, Any]) -> tuple[bool, str]:
    calls = tool_calls(test_run)
    names = [str(call.get("tool_name")) for call in calls]
    if not calls:
        return False, "geen toolcall"
    if names[0] != case.expected_tool:
        return False, f"eerste tool {names[0]!r}, verwacht {case.expected_tool!r}"
    forbidden = sorted(set(names) & set(case.forbidden_tools))
    if forbidden:
        return False, f"verboden toolcall(s): {', '.join(forbidden)}"

    params = parse_params(calls[0])
    for path, expected in case.expected_values:
        actual = expected_value(params, path)
        if (
            path == "operations.0.op"
            and expected == "set_layout_template"
            and actual == "set_meta"
            and nested_value(params, "operations.0.values.layout_template") is not None
        ):
            actual = "set_layout_template"
        if (
            path == "operations.0.values.panel_type"
            and expected == "map_2d"
            and actual is None
            and nested_value(params, "operations.0.op") == "set_map_mode"
        ):
            actual = "map_2d"
        if actual != expected:
            return False, f"{path}={actual!r}, verwacht {expected!r}"
    for path, expected_items in case.expected_sets:
        actual = nested_value(params, path)
        if not isinstance(actual, list) or set(map(str, actual)) != set(expected_items):
            return False, f"{path}={actual!r}, verwacht set {list(expected_items)!r}"
    if case.query_terms:
        query = str(params.get("query") or "").casefold()
        missing = [term for term in case.query_terms if term.casefold() not in query]
        if missing:
            return False, f"zoekquery mist: {', '.join(missing)}"

    if case.operations:
        raw_operations = params.get("operations")
        if not isinstance(raw_operations, list) or not raw_operations:
            return False, "data_batch bevat geen operaties"
        operations = [item for item in raw_operations if isinstance(item, dict)]
        for expectation in case.operations:
            operation = matching_operation(operations, expectation)
            if operation is None:
                metric_detail = (
                    f" met metric {expectation.metric}" if expectation.metric is not None else ""
                )
                return False, (
                    f"mist {expectation.operation} voor {expectation.stream}{metric_detail}; "
                    f"ontvangen={[(item.get('operation'), sorted(operation_streams(item))) for item in operations]!r}"
                )
            for field in ("metric", "sort", "order"):
                expected = getattr(expectation, field)
                if field == "metric" and expected is not None:
                    actual_matches = expected in operation_metrics(operation)
                else:
                    actual_matches = expected is None or operation.get(field) == expected
                if not actual_matches:
                    return False, (
                        f"{expectation.stream}.{field}={operation.get(field)!r}, verwacht {expected!r}"
                    )
            if expectation.limit_max is not None:
                limit = operation.get("limit")
                if not isinstance(limit, int) or limit > expectation.limit_max:
                    return False, (
                        f"{expectation.stream}.limit={limit!r}, maximaal {expectation.limit_max}"
                    )
        if case.name in {"p2000-cross-source-radius", "knmi-cross-source-radius"}:
            nearby_operations = [
                item for item in operations if item.get("operation") == "query_nearby"
            ]
            for operation in nearby_operations:
                origin_handle = str(operation.get("origin_handle") or "")
                radius_m = operation.get("radius_m")
                if not origin_handle.startswith("@"):
                    return False, "query_nearby mist een @origin-handle"
                if not isinstance(radius_m, int) or not 1 <= radius_m <= 10_000:
                    return False, f"query_nearby radius_m={radius_m!r}, verwacht 1..10000"
        if case.name == "lucht-no2-pm25-correlation":
            correlation = next(
                (item for item in operations if item.get("operation") == "correlate"), None
            )
            aliases = {
                str(item.get("save_as", "")).removeprefix("@"): operation_metrics(item)
                for item in operations
                if item.get("save_as")
            }
            correlation_metrics: set[str] = set()
            for field in ("series_a", "series_b"):
                series = str((correlation or {}).get(field, ""))
                if series.startswith("@"):
                    correlation_metrics.update(aliases.get(series.removeprefix("@"), set()))
                elif series:
                    correlation_metrics.add(series)
            if not {"no2_ug_m3", "pm25_ug_m3"} <= correlation_metrics:
                return False, "correlatie mist no2_ug_m3 of pm25_ug_m3"
    return True, " -> ".join(names)


def case_fingerprint(case: AgentCase) -> str:
    payload = {"validator_version": VALIDATOR_VERSION, "case": asdict(case)}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def load_results(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"cases": {}}
    return value if isinstance(value, dict) else {"cases": {}}


def select_cases(
    previous: dict[str, Any], *, force_all: bool, requested: set[str]
) -> list[AgentCase]:
    records = previous.get("cases") if isinstance(previous.get("cases"), dict) else {}
    selected: list[AgentCase] = []
    for case in AGENT_CASES:
        if requested:
            if case.name in requested:
                selected.append(case)
            continue
        record = records.get(case.name) if isinstance(records, dict) else None
        if (
            force_all
            or not isinstance(record, dict)
            or record.get("status") != "pass"
            or record.get("fingerprint") != case_fingerprint(case)
        ):
            selected.append(case)
    return selected


def save_results(path: Path, previous: dict[str, Any], updates: dict[str, Any]) -> None:
    records = previous.get("cases") if isinstance(previous.get("cases"), dict) else {}
    merged = {**records, **updates}
    payload = {
        "suite": "elevenlabs-agent-acceptance",
        "validator_version": VALIDATOR_VERSION,
        "updated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "total": len(AGENT_CASES),
            "passed": sum(record.get("status") == "pass" for record in merged.values()),
            "failed": sum(record.get("status") == "fail" for record in merged.values()),
        },
        "cases": merged,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def create_test(client: httpx.Client, case: AgentCase, tool_id: str, prefix: str) -> str:
    payload = {
        "type": "tool",
        "name": f"{prefix}-{case.name}",
        "chat_history": [{"role": "user", "message": case.prompt, "time_in_call_secs": 0}],
        "tool_call_parameters": {
            "referenced_tool": {"id": tool_id, "type": "client"},
            "parameters": [],
            "verify_absence": False,
        },
        "check_any_tool_matches": False,
    }
    result = request(client, "POST", "/v1/convai/agent-testing/create", json=payload)
    return str(result["id"])


def wait_for_invocation(
    client: httpx.Client, invocation_id: str, timeout_seconds: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = request(client, "GET", f"/v1/convai/test-invocations/{invocation_id}")
        runs = result.get("test_runs", [])
        if runs and all(run.get("status") != "pending" for run in runs):
            return result
        time.sleep(2)
    raise TimeoutError(f"ElevenLabs-testinvocation {invocation_id} duurde te lang.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Test toolkeuze en parameters van de live ElevenLabs-agent. "
            "Geslaagde, ongewijzigde cases worden standaard niet opnieuw uitgevoerd."
        )
    )
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--keep-tests", action="store_true")
    parser.add_argument("--all", action="store_true", help="Voer ook eerder geslaagde cases uit.")
    parser.add_argument("--case", action="append", default=[], help="Voer alleen deze case uit.")
    parser.add_argument("--list", action="store_true", help="Toon cases zonder API-calls.")
    parser.add_argument(
        "--pending",
        action="store_true",
        help="Toon alleen cases die een normale run zou uitvoeren, zonder API-calls.",
    )
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    known_names = {case.name for case in AGENT_CASES}
    requested = set(args.case)
    unknown = sorted(requested - known_names)
    if unknown:
        print(f"Onbekende case(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    if args.list:
        for case in AGENT_CASES:
            print(f"{case.name:<30} {case.expected_tool:<20} {case.prompt}")
        return 0

    previous = load_results(args.results)
    selected = select_cases(previous, force_all=args.all, requested=requested)
    if args.pending:
        if not selected:
            print("Geen nieuwe, gewijzigde of mislukte cases.")
        for case in selected:
            print(f"{case.name:<30} {case.expected_tool:<20} {case.prompt}")
        return 0
    if not selected:
        print(
            f"Alle {len(AGENT_CASES)} ongewijzigde cases zijn al geslaagd; "
            "geen ElevenLabs-call uitgevoerd."
        )
        return 0

    settings = get_settings()
    if not settings.elevenlabs_api_key or not settings.elevenlabs_agent_id:
        print("ELEVENLABS_API_KEY of ELEVENLABS_AGENT_ID ontbreekt.", file=sys.stderr)
        return 2
    headers = {
        "xi-api-key": settings.elevenlabs_api_key,
        "Content-Type": "application/json",
    }
    prefix = f"talk2dashboard-acceptance-{int(time.time())}"
    created: list[str] = []
    cases_by_test_id: dict[str, AgentCase] = {}
    updates: dict[str, Any] = {}
    with httpx.Client(timeout=30, headers=headers) as client:
        try:
            tools_payload = request(client, "GET", "/v1/convai/tools")
            tool_ids = {
                item.get("tool_config", {}).get("name"): item.get("id")
                for item in tools_payload.get("tools", [])
            }
            missing = sorted({case.expected_tool for case in selected} - set(tool_ids))
            if missing:
                raise RuntimeError(f"ElevenLabs-tools ontbreken: {', '.join(missing)}")
            for case in selected:
                test_id = create_test(client, case, str(tool_ids[case.expected_tool]), prefix)
                created.append(test_id)
                cases_by_test_id[test_id] = case
            run = request(
                client,
                "POST",
                f"/v1/convai/agents/{settings.elevenlabs_agent_id}/run-tests",
                json={"tests": [{"test_id": test_id} for test_id in created]},
            )
            result = wait_for_invocation(client, str(run["id"]), args.timeout)
            runs_by_id = {str(item.get("test_id")): item for item in result["test_runs"]}
            failures = 0
            print(
                f"{len(selected)}/{len(AGENT_CASES)} cases geselecteerd; eerdere PASS-cases overgeslagen."
            )
            print("case                            resultaat  detail")
            print(
                "------------------------------  ---------  ------------------------------------------"
            )
            tested_at = datetime.now(UTC).isoformat()
            for test_id in created:
                case = cases_by_test_id[test_id]
                test_run = runs_by_id.get(test_id, {})
                ok, detail = validate(case, test_run)
                failures += int(not ok)
                updates[case.name] = {
                    "status": "pass" if ok else "fail",
                    "detail": detail,
                    "fingerprint": case_fingerprint(case),
                    "tested_at": tested_at,
                }
                if not ok:
                    updates[case.name]["observed_tool_calls"] = [
                        {
                            "tool_name": call.get("tool_name"),
                            "params": parse_params(call),
                        }
                        for call in tool_calls(test_run)[:3]
                    ]
                print(f"{case.name:<30}  {'PASS' if ok else 'FAIL':<9}  {detail}")
            save_results(args.results, previous, updates)
            print(
                f"\n{len(selected) - failures}/{len(selected)} geselecteerde cases geslaagd. "
                f"Resultaten: {args.results}"
            )
            if failures:
                print("Een volgende standaardrun voert alleen FAIL- of gewijzigde cases uit.")
            return 1 if failures else 0
        finally:
            if not args.keep_tests:
                for test_id in created:
                    try:
                        request(client, "DELETE", f"/v1/convai/agent-testing/{test_id}")
                    except RuntimeError as exc:
                        print(
                            f"Waarschuwing: tijdelijke test niet verwijderd: {exc}",
                            file=sys.stderr,
                        )


if __name__ == "__main__":
    raise SystemExit(main())
