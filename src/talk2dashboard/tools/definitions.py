from __future__ import annotations

from typing import Any

from talk2dashboard.agent_catalog import (
    MEASUREMENT_METRICS,
    NEARBY_PLACE_TYPES,
    PANEL_TYPES,
    STREAM_IDS,
)

LEGACY_TOOL_NAMES = frozenset(
    {
        "set_dashboard_view",
        "filter_incidents",
        "highlight_region",
        "get_dashboard_state",
        "reset_dashboard",
        "web_search",
    }
)


def _filter_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "field": {
                "type": "string",
                "description": "Recordveld, eventueel genest zoals location.label.",
            },
            "op": {
                "type": "string",
                "enum": ["eq", "in", "gte", "lte", "between", "contains", "within_radius_handle"],
                "description": "Deterministische filteroperator.",
            },
            "value": {"type": "string", "description": "Tekstuele vergelijkingswaarde."},
            "value_number": {"type": "number", "description": "Numerieke vergelijkingswaarde."},
            "values": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Waarden voor de in-operator.",
            },
            "handle_id": {
                "type": "string",
                "description": "Origin-handle voor within_radius_handle.",
            },
            "origin_record_id": {
                "type": "string",
                "description": "Optioneel specifiek record uit de origin-handle.",
            },
            "radius_m": {
                "type": "integer",
                "minimum": 1,
                "maximum": 25000,
                "description": "Straal in meters, maximaal vijfentwintig kilometer.",
            },
        },
        "required": ["field", "op"],
    }


def _data_operation_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "query_events",
                    "query_measurements",
                    "query_nearby",
                    "aggregate",
                    "baseline",
                    "correlate",
                    "get_incident",
                    "diff",
                    "answer_slice",
                    "resolve_location",
                ],
                "description": "Read-only bewerking. Bij binnen-een-straalvragen is query_nearby verplicht voor iedere doelstream; gewone queries filteren niet op afstand.",
            },
            "stream": {
                "type": "string",
                "enum": list(STREAM_IDS),
                "description": "Een bekende databron.",
            },
            "streams": {
                "type": "array",
                "items": {"type": "string", "enum": list(STREAM_IDS)},
                "description": "Meerdere bekende databronnen.",
            },
            "metric": {
                "type": "string",
                "description": "Canonieke meetwaarde-ID. Bekende waarden: "
                + ", ".join(MEASUREMENT_METRICS)
                + ". Luchtmeetnet kan later extra <formule>_ug_m3-codes publiceren.",
            },
            "metrics": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Meerdere canonieke meetwaarde-IDs. Bekende waarden: "
                + ", ".join(MEASUREMENT_METRICS)
                + ".",
            },
            "window": {
                "type": "string",
                "description": "ISO-duur zoals PT60M, PT6H, PT24H of P14D.",
            },
            "category": {"type": "string", "description": "Exacte eventcategorie."},
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Toegestane eventcategorieen.",
            },
            "severity": {"type": "string", "description": "Eventernst, bijvoorbeeld high."},
            "status": {"type": "string", "description": "Eventstatus, bijvoorbeeld active."},
            "text": {
                "type": "string",
                "description": "Vrije tekstfilter of te resolveren locatie.",
            },
            "filters": {"type": "array", "items": _filter_schema()},
            "sort": {
                "type": "string",
                "description": "Sorteerveld, meestal value, observed_at, title of location.label.",
            },
            "order": {"type": "string", "enum": ["asc", "desc"]},
            "limit": {
                "type": "integer",
                "description": "Maximaal aantal rijen, een tot tweeduizend.",
            },
            "group_by": {
                "type": "string",
                "description": "Groeperingsveld zoals stream_id, category, metric of location.label.",
            },
            "fn": {
                "type": "string",
                "enum": [
                    "count",
                    "sum",
                    "mean",
                    "median",
                    "min",
                    "max",
                    "p95",
                    "latest",
                    "delta",
                    "percent_change",
                ],
                "description": "Deterministische aggregatiefunctie.",
            },
            "input_handle": {"type": "string", "description": "Bestaande inputhandle."},
            "origin_handle": {
                "type": "string",
                "description": "Origin-handle of @alias voor query_nearby.",
            },
            "origin_resolution_id": {
                "type": "string",
                "description": "Tijdelijke locres_-ID of @alias voor een gegeocodeerde oorsprong.",
            },
            "origin_text": {
                "type": "string",
                "description": "Adres of plaats voor een tijdelijke gegeocodeerde query_nearby-oorsprong.",
            },
            "origin_record_id": {
                "type": "string",
                "description": "Optioneel specifiek middelpuntrecord uit de origin-handle.",
            },
            "radius_m": {
                "type": "integer",
                "minimum": 1,
                "maximum": 25000,
                "description": "Zoekstraal voor query_nearby, maximaal vijfentwintig kilometer.",
            },
            "series_a": {"type": "string", "description": "Metric of handle voor correlatie."},
            "series_b": {"type": "string", "description": "Metric of handle voor correlatie."},
            "left_handle": {"type": "string", "description": "Linker handle voor diff."},
            "right_handle": {"type": "string", "description": "Rechter handle voor diff."},
            "incident_id": {"type": "string", "description": "Incident-ID voor get_incident."},
            "resolver": {"type": "string", "enum": ["google"]},
            "save_as": {
                "type": "string",
                "description": "Korte alias. Verwijs binnen dezelfde batch als @alias.",
            },
        },
        "required": ["operation"],
    }


def _field_bindings_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "x": {"type": "string", "description": "X-veld, meestal observed_at."},
            "y": {"type": "string", "description": "Numeriek veld, meestal value."},
            "label": {
                "type": "string",
                "description": "Labelveld, bijvoorbeeld location.label, title of group.",
            },
            "series": {
                "type": "string",
                "description": "Optioneel reeksveld voor een tijdgrafiek, meestal location.label of metric.",
            },
            "time": {"type": "string", "description": "Tijdveld, meestal observed_at."},
            "latitude": {"type": "string", "description": "location.latitude"},
            "longitude": {"type": "string", "description": "location.longitude"},
        },
    }


def _panel_binding_schema(*, require_handle: bool = False) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "seed_handle_id": {
                "type": "string",
                "description": "Handle-ID uit het voorgaande data_batch-resultaat.",
            },
            "field_bindings": _field_bindings_schema(),
            "refresh_policy": {
                "type": "string",
                "enum": ["visible", "manual", "paused"],
            },
        },
    }
    if require_handle:
        schema["required"] = ["seed_handle_id"]
    return schema


def _dashboard_operation_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "op": {
                "type": "string",
                "enum": [
                    "set_meta",
                    "set_layout_template",
                    "upsert_panel",
                    "remove_panel",
                    "set_global_filter",
                    "set_focus",
                    "set_map_mode",
                    "undo",
                ],
            },
            "panel_id": {"type": "string", "description": "Stabiele semantische panel-ID."},
            "panel_type": {
                "type": "string",
                "enum": list(PANEL_TYPES),
                "description": "Gevalideerd paneltype. Gebruik ranking voor een ranglijst.",
            },
            "title": {"type": "string", "description": "Korte Nederlandse paneltitel."},
            "seed_handle_id": {
                "type": "string",
                "description": "Handle-ID uit het voorgaande data_batch-resultaat.",
            },
            "binding": _panel_binding_schema(),
            "bindings": {
                "type": "array",
                "minItems": 1,
                "maxItems": 6,
                "items": _panel_binding_schema(require_handle=True),
                "description": (
                    "Een tot zes onafhankelijke databindings voor een samengestelde kaart, "
                    "feed, ranglijst, tijdreeks, vergelijking, KPI- of evidenceweergave. "
                    "Iedere handle houdt eigen kleur, herkomst en refreshstatus. Niet "
                    "gebruiken voor source_health of correlation."
                ),
            },
            "span": {
                "type": "string",
                "enum": ["compact", "standard", "wide", "full"],
            },
            "order": {"type": "integer", "description": "Volgorde van nul tot honderd."},
            "props": {
                "type": "object",
                "properties": {
                    "unit": {"type": "string", "description": "Weergave-eenheid."},
                },
            },
            "values": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "subtitle": {"type": "string"},
                    "layout_template": {
                        "type": "string",
                        "enum": [
                            "incident_focus",
                            "trend_focus",
                            "mobility_focus",
                            "environment_focus",
                            "evidence_focus",
                            "briefing",
                        ],
                    },
                    "map_focus": {"type": "string"},
                    "panel_id": {"type": "string"},
                    "panel_type": {"type": "string", "enum": ["map_2d", "map_3d_google"]},
                },
            },
        },
        "required": ["op"],
    }


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "inspect_workspace",
        "display_name": "Werkruimte bekijken",
        "category": "Inzicht",
        "description": "Gebruik alleen voor expliciete vragen over beschikbare data/status/configuratie, onbekende IDs of incidentdetails. Niet gebruiken voor bekende streams, metrics, velden of paneltypen; die staan al in de prompt en data_batch-schema's.",
        "parameters": {
            "type": "object",
            "properties": {
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "streams",
                            "stream_schema",
                            "panel_types",
                            "dashboard",
                            "policies",
                            "incidents",
                        ],
                    },
                },
                "ids": {"type": "array", "items": {"type": "string"}},
                "detail": {"type": "string", "enum": ["ids", "compact", "full"]},
            },
            "required": ["sections"],
        },
    },
    {
        "name": "data_batch",
        "display_name": "Data analyseren",
        "category": "Inzicht",
        "description": "Primaire read-only datatool. Query bekende streams direct zonder inspect_workspace. Een ranglijst gebruikt query_measurements plus sort value, order desc en limit. Het resultaat bevat handles, previews, freshness en bronstatus voor onderbouwing.",
        "parameters": {
            "type": "object",
            "properties": {"operations": {"type": "array", "items": _data_operation_schema()}},
            "required": ["operations"],
        },
    },
    {
        "name": "dashboard_batch",
        "display_name": "Dashboard aanpassen",
        "category": "Presentatie",
        "description": "Pas de dashboardweergave atomair aan met server-issued handles. Gebruik ranking voor ranglijsten, timeseries voor tijdreeksen en event_table/incident_timeline voor meldingen. Een places-handle uit nearby_places mag uitsluitend naar nearby_places, map_2d of map_3d_google. Een web_results-handle uit external_search mag uitsluitend zonder veldbindings naar evidence. Gebruik beide nooit als kpi. De client vult expected_version automatisch in.",
        "parameters": {
            "type": "object",
            "properties": {
                "expected_version": {
                    "type": "integer",
                    "description": "Automatisch door de browser ingevuld; niet zelf opzoeken.",
                },
                "operations": {"type": "array", "items": _dashboard_operation_schema()},
                "composition_mode": {
                    "type": "string",
                    "enum": ["adaptive", "merge", "replace_visible"],
                    "description": "adaptive bewaakt maximaal twaalf panelen; merge voegt expliciet toe; replace_visible vervangt de huidige zichtbare werkruimte.",
                },
                "reason": {"type": "string"},
            },
            "required": ["operations", "reason"],
        },
    },
    {
        "name": "nearby_places",
        "display_name": "Voorzieningen in de buurt",
        "category": "Externe context",
        "description": "Zoek maximaal vijftien Google Places binnen maximaal vijfentwintig kilometer. Geef voor een gewone plaatsnaam direct origin_text op; geef een locres_-resultaat uitsluitend als resolution_id. Het resultaat bevat afstand_m en nearest. Gebruik external_search niet als fallback.",
        "parameters": {
            "type": "object",
            "properties": {
                "origin_handle": {"type": "string"},
                "location_ref": {"type": "string"},
                "resolution_id": {"type": "string"},
                "origin_text": {
                    "type": "string",
                    "description": "Nederlandse plaats- of adresnaam die tijdelijk via Google Geocoding wordt opgelost.",
                },
                "included_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(NEARBY_PLACE_TYPES)},
                    "minItems": 1,
                    "uniqueItems": True,
                    "description": "Gebruik uitsluitend de opgesomde Google Place-hoofdtypen.",
                },
                "radius_m": {"type": "integer", "minimum": 1, "maximum": 25000},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 15},
                "rank": {"type": "string", "enum": ["distance", "popularity"]},
                "fields_profile": {"type": "string", "enum": ["minimal", "contact", "operational"]},
            },
            "required": ["included_types"],
        },
    },
    {
        "name": "capture_dashboard",
        "display_name": "Dashboard vastleggen",
        "category": "Presentatie",
        "description": "Maak een reproduceerbare screenshot en structured snapshot van een dashboardversie.",
        "parameters": {
            "type": "object",
            "properties": {
                "dashboard_version": {"type": "integer"},
                "scope": {"type": "string", "enum": ["viewport", "full_dashboard", "panel"]},
                "panel_id": {"type": "string"},
                "include_structured_state": {"type": "boolean"},
                "analyze": {"type": "boolean"},
                "wait_for_render_ms": {"type": "integer"},
            },
            "required": ["dashboard_version", "scope"],
        },
    },
    {
        "name": "external_search",
        "display_name": "Actuele webcontext zoeken",
        "category": "Externe context",
        "description": "Zoek maximaal vijf actuele publieke webresultaten wanneer de gebruiker websearch heeft ingeschakeld. Het resultaat is onbevestigde context en mag op het dashboard uitsluitend zonder veldbindings aan evidence worden gekoppeld, nooit aan een meldingenfeed, kaart of grafiek.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
                "recency_days": {"type": "integer"},
                "domain_allowlist": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["query"],
        },
    },
]
