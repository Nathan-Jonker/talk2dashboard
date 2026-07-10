from __future__ import annotations

from typing import Any

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "inspect_workspace",
        "description": "Inspecteer alleen de benodigde streams, schemas, paneltypes, bronstatus of dashboardstate.",
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
        "description": "Voer meerdere read-only dataqueries, aggregaties, baselines of correlaties in een batch uit.",
        "parameters": {
            "type": "object",
            "properties": {"operations": {"type": "array", "items": {"type": "object"}}},
            "required": ["operations"],
        },
    },
    {
        "name": "dashboard_batch",
        "description": "Pas dashboardweergave atomair aan zonder brondata te wijzigen.",
        "parameters": {
            "type": "object",
            "properties": {
                "expected_version": {"type": "integer"},
                "operations": {"type": "array", "items": {"type": "object"}},
                "reason": {"type": "string"},
            },
            "required": ["expected_version", "operations", "reason"],
        },
    },
    {
        "name": "nearby_places",
        "description": "Zoek maximaal twintig Google Places binnen maximaal vijf kilometer rond een bestaande locatiehandle.",
        "parameters": {
            "type": "object",
            "properties": {
                "origin_handle": {"type": "string"},
                "location_ref": {"type": "string"},
                "included_types": {"type": "array", "items": {"type": "string"}},
                "radius_m": {"type": "integer"},
                "max_results": {"type": "integer"},
                "rank": {"type": "string", "enum": ["distance", "popularity"]},
                "fields_profile": {"type": "string", "enum": ["minimal", "contact", "operational"]},
            },
            "required": ["included_types", "radius_m"],
        },
    },
    {
        "name": "capture_dashboard",
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
        "description": "Zoek maximaal vijf actuele publieke webresultaten wanneer de gebruiker websearch heeft ingeschakeld.",
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
