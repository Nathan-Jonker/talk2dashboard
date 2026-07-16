from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import parse_qs

import dash_ag_grid as dag
import plotly.graph_objects as go
import vizro.models as vm
from dash import Input, Output, callback, dcc, html
from vizro import Vizro

from talk2dashboard.dashboard import DashboardService
from talk2dashboard.domain import MAX_VISIBLE_PANELS, PanelSpec
from talk2dashboard.panel_contracts import evaluate_panel, nested_value
from talk2dashboard.query import QueryEngine
from talk2dashboard.sources.service import SourceService

_data_div = cast(Any, html.Div)
_data_button = cast(Any, html.Button)
_data_li = cast(Any, html.Li)


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if text := data.strip():
            self.parts.append(text)


def _plain_text(value: object) -> str:
    parser = _PlainTextParser()
    parser.feed(unescape(str(value or "")))
    return " ".join(parser.parts)


def _nested_value(row: dict[str, Any], field: str) -> object | None:
    return nested_value(row, field)


_METRIC_LABELS = {
    "wind_gust_kmh": "windstoot",
    "wind_speed_ms": "windsnelheid",
    "rainfall_rate_mm_h": "neerslagintensiteit",
    "air_temperature_c": "temperatuur",
    "water_level_cm": "waterstand",
    "no2_ug_m3": "stikstofdioxide",
    "no_ug_m3": "stikstofmonoxide",
    "nox_ug_m3": "stikstofoxiden",
    "o3_ug_m3": "ozon",
    "pm10_ug_m3": "fijnstof PM10",
    "pm25_ug_m3": "fijnstof PM2,5",
    "so2_ug_m3": "zwaveldioxide",
    "co_ug_m3": "koolmonoxide",
}

_MAP_PANEL_TYPES = frozenset({"map_2d", "map_3d_google"})
_FEED_PANEL_TYPES = frozenset({"incident_timeline", "event_table", "change_summary", "ai_brief"})
_TREND_PANEL_TYPES = frozenset({"timeseries", "ranking", "comparison"})
_INSIGHT_PANEL_TYPES = frozenset({"kpi", "correlation", "comparison", "ranking"})

_PRIMARY_PANEL_PRIORITIES: dict[str, tuple[frozenset[str], ...]] = {
    "incident_focus": (_MAP_PANEL_TYPES, _FEED_PANEL_TYPES, _TREND_PANEL_TYPES),
    "mobility_focus": (_MAP_PANEL_TYPES, _FEED_PANEL_TYPES, _TREND_PANEL_TYPES),
    "trend_focus": (_TREND_PANEL_TYPES, _MAP_PANEL_TYPES, _FEED_PANEL_TYPES),
    "environment_focus": (_TREND_PANEL_TYPES, _MAP_PANEL_TYPES, _INSIGHT_PANEL_TYPES),
    "briefing": (_FEED_PANEL_TYPES, _MAP_PANEL_TYPES, _TREND_PANEL_TYPES),
    "evidence_focus": (_INSIGHT_PANEL_TYPES, _TREND_PANEL_TYPES, _FEED_PANEL_TYPES),
}


def _control_room_slots(panels: list[PanelSpec], layout_template: str) -> dict[str, str]:
    """Assign ephemeral presentation slots without changing persisted panel order."""
    if not panels:
        return {}

    priorities = _PRIMARY_PANEL_PRIORITIES.get(
        layout_template,
        (_MAP_PANEL_TYPES, _TREND_PANEL_TYPES, _FEED_PANEL_TYPES),
    )
    primary = next(
        (
            panel
            for panel_types in priorities
            for panel in panels
            if panel.panel_type in panel_types
        ),
        panels[0],
    )
    remaining = [panel for panel in panels if panel.panel_id != primary.panel_id]

    left_capacity = min(5, len(remaining) // 2)

    feed_panels = [panel for panel in remaining if panel.panel_type in _FEED_PANEL_TYPES]
    left = feed_panels[:left_capacity]
    if len(left) < left_capacity:
        left_ids = {panel.panel_id for panel in left}
        left.extend(panel for panel in remaining if panel.panel_id not in left_ids)
        left = left[:left_capacity]

    left_ids = {panel.panel_id for panel in left}
    right = [panel for panel in remaining if panel.panel_id not in left_ids]
    slots = {primary.panel_id: "primary"}
    slots.update({panel.panel_id: f"left-{index}" for index, panel in enumerate(left, 1)})
    slots.update({panel.panel_id: f"right-{index}" for index, panel in enumerate(right, 1)})
    return slots


def _metric_label(metric: object) -> str:
    value = str(metric or "meting")
    return _METRIC_LABELS.get(value, value.replace("_ug_m3", "").replace("_", " "))


def _measurement_title(row: dict[str, Any]) -> str:
    if row.get("title"):
        return str(row["title"])
    location = row.get("location") or {}
    location_label = location.get("label") if isinstance(location, dict) else None
    metric = _metric_label(row.get("metric"))
    value = row.get("value")
    unit = row.get("unit") or ""
    measurement = f"{value:g} {unit}".strip() if isinstance(value, (int, float)) else metric
    return " · ".join(str(item) for item in (location_label, metric, measurement) if item)


@dataclass(slots=True)
class RendererContext:
    dashboard: DashboardService
    query: QueryEngine
    sources: SourceService


class OperationalShell(vm.VizroBaseModel):
    type: Literal["operational_shell"] = "operational_shell"

    def build(self):
        return html.Div(
            className="talk2d-shell",
            children=[
                dcc.Store(id="dashboard-event-store"),
                _data_div(id="render-context", style={"display": "none"}),
                dcc.Location(id="page-location", refresh=False),
                html.Header(
                    className="talk2d-topbar",
                    children=[
                        html.Div(
                            className="talk2d-wordmark",
                            children=[
                                html.H1("Talk2Dashboard NL"),
                                html.P("spraak · bronnen · operationeel beeld"),
                            ],
                        ),
                        html.Div(
                            className="talk2d-status-row",
                            children=[
                                html.Span(
                                    "bronstatus laden",
                                    id="source-status",
                                    className="talk2d-status",
                                ),
                                html.Span(
                                    "bijwerktijd laden",
                                    id="version-status",
                                    className="talk2d-status",
                                ),
                            ],
                        ),
                    ],
                ),
                html.Section(
                    className="talk2d-heading",
                    children=[
                        html.Div(
                            children=[
                                html.H2("Operationeel beeld", id="talk2d-dashboard-title"),
                                html.P(
                                    "Dashboardstate via gevalideerde tools", id="dashboard-subtitle"
                                ),
                            ]
                        ),
                    ],
                ),
                html.Main(
                    className="talk2d-main",
                    children=[html.Div(id="panel-host", className="talk2d-panel-host")],
                ),
                _data_div(id="voice-dock-root", **{"data-agent-mode": "elevenlabs"}),
            ],
        )


vm.Page.add_type("components", OperationalShell)


def _panel_frame(
    panel,
    body,
    handle=None,
    warning: str | None = None,
    *,
    handle_ids: list[str] | None = None,
    meta_override: str | None = None,
):
    meta = meta_override or "Actuele werkruimte"
    if handle:
        newest = handle.freshness.get("newest")
        record_label = "record" if handle.row_count == 1 else "records"
        if newest:
            timestamp_label = f"bijgewerkt {_display_time(newest)}"
        elif handle.kind in {"places", "web_results"}:
            timestamp_label = f"opgevraagd {_display_time(handle.created_at)}"
        else:
            timestamp_label = "bijgewerkt onbekend"
        meta = meta_override or f"{handle.row_count} {record_label} · {timestamp_label}"
    all_handle_ids = handle_ids or ([handle.handle_id] if handle else [])
    return html.Section(
        id={"type": "dashboard-panel", "index": panel.panel_id},
        className="talk2d-panel",
        **{
            "data-span": panel.span,
            "data-panel-type": panel.panel_type,
            "data-panel-id": panel.panel_id,
            "data-layout-slot": panel.props.get("_layout_slot", "support"),
            "data-handle-id": handle.handle_id if handle else "",
            "data-handle-ids": json.dumps(all_handle_ids, ensure_ascii=True),
        },
        children=[
            html.Header(
                className="talk2d-panel__head",
                children=[html.H3(panel.title), html.Span(meta, className="talk2d-panel__meta")],
            ),
            html.Div(
                [
                    html.Div(warning, className="talk2d-stale") if warning else None,
                    body,
                ],
                className="talk2d-panel__body",
            ),
        ],
    )


def _figure_layout() -> dict:
    return {
        "margin": {"l": 40, "r": 12, "t": 8, "b": 34},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {
            "family": "-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
            "color": "#526778",
            "size": 11,
        },
        "xaxis": {
            "gridcolor": "#dce5ea",
            "linecolor": "#aebdc7",
            "zeroline": False,
            "automargin": True,
        },
        "yaxis": {
            "gridcolor": "#dce5ea",
            "linecolor": "#aebdc7",
            "zeroline": False,
            "automargin": True,
        },
        "showlegend": False,
        "uirevision": "stable",
    }


def _display_time(value: object) -> str:
    if value in {None, ""}:
        return "onbekend"
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone().strftime("%H:%M")
    except ValueError:
        return text[11:16] or "onbekend"


def _source_status_label(status: object) -> str:
    return {
        "healthy": "actueel",
        "stale": "verouderd",
        "fixture": "demodata",
        "degraded": "beperkt",
        "offline": "offline",
        "disabled": "uitgeschakeld",
        "starting": "starten",
    }.get(str(status), str(status))


def _source_short_name(owner: str) -> str:
    return {
        "Koninklijk Nederlands Meteorologisch Instituut": "KNMI",
        "Nationaal Dataportaal Wegverkeer": "NDW",
        "Nederlandse Spoorwegen": "NS",
        "RIVM / Luchtmeetnet": "Luchtmeetnet",
        "Alarmeringen.nl": "P2000",
    }.get(owner, owner)


def _source_from_row(row: dict) -> dict:
    source = row.get("source_ref") or {}
    return source if isinstance(source, dict) else {}


def _feed_description(row: dict, source: dict) -> str:
    description = _plain_text(row.get("description"))
    if description:
        return description
    stream_id = str(source.get("stream_id") or row.get("stream_id") or "")
    title = str(row.get("title") or "").casefold()
    if stream_id == "p2000":
        if title.startswith(("a1 ", "a2 ")) or "ambu" in title:
            return "Ambulance-inzet gemeld via P2000."
        if "brandweer" in title:
            return "Brandweer-inzet gemeld via P2000."
        return "Melding voor hulpdiensten via P2000."
    if stream_id == "ndw_incidents":
        return "Actuele verkeerssituatie uit de officiële NDW-feed."
    return f"Actuele melding van {_source_short_name(str(source.get('owner') or 'de bron'))}."


_MAP_SOURCE_COLORS = {
    "p2000": "#e75b43",
    "ndw_incidents": "#0878be",
    "ns_disruptions": "#f7d417",
    "knmi_observations": "#00a6a6",
    "rws_water": "#003b5c",
    "luchtmeetnet": "#2f855a",
    "nos_rss": "#c98300",
    "google_places": "#5f6f7c",
}
_MAP_FALLBACK_COLORS = ("#0878be", "#00a6a6", "#e75b43", "#f7d417", "#2f855a")
_STREAM_SOURCE_LABELS = {
    "p2000": "P2000",
    "ndw_incidents": "NDW",
    "ns_disruptions": "NS",
    "knmi_observations": "KNMI",
    "rws_water": "Rijkswaterstaat",
    "luchtmeetnet": "Luchtmeetnet",
    "nos_rss": "NOS",
    "google_places": "Google Places",
}


def _map_source_key(row: dict[str, Any], handle_kind: str) -> str:
    source = _source_from_row(row)
    if handle_kind == "places":
        return "google_places"
    return str(source.get("stream_id") or row.get("stream_id") or handle_kind or "bron")


def _map_source_label(row: dict[str, Any], handle_kind: str) -> str:
    source = _source_from_row(row)
    if handle_kind == "places":
        return "Google Places"
    owner = str(source.get("owner") or "")
    return _source_short_name(owner) if owner else _map_source_key(row, handle_kind)


def _map_source_color(source_key: str) -> str:
    if source_key in _MAP_SOURCE_COLORS:
        return _MAP_SOURCE_COLORS[source_key]
    checksum = sum((index + 1) * ord(char) for index, char in enumerate(source_key))
    return _MAP_FALLBACK_COLORS[checksum % len(_MAP_FALLBACK_COLORS)]


def _binding_source_identity(
    binding: Any, handle: Any, rows: list[dict[str, Any]]
) -> tuple[str, str]:
    if rows:
        source_key = _map_source_key(rows[0], handle.kind)
        source_label = _map_source_label(rows[0], handle.kind)
        if source_key not in {str(handle.kind), "bron"}:
            return source_key, source_label
    query_spec = binding.query_spec if binding else {}
    stream = query_spec.get("stream")
    if not stream:
        streams = query_spec.get("streams") or []
        stream = streams[0] if len(streams) == 1 else None
    source_key = str(stream or handle.kind or "bron")
    return source_key, _STREAM_SOURCE_LABELS.get(source_key, source_key.replace("_", " "))


@dataclass(slots=True)
class _PanelLayer:
    handle: Any
    binding: Any
    rows: list[dict[str, Any]]
    source_key: str
    source_label: str
    color: str
    warning: str | None = None


def _load_panel_layers(
    panel: PanelSpec,
    context: RendererContext,
    materialized: dict[str, dict],
) -> tuple[list[_PanelLayer], list[str]]:
    layers: list[_PanelLayer] = []
    errors: list[str] = []
    materialized_layers = _panel_materialized_layers(panel.panel_id, materialized)
    for index, handle_data in enumerate(materialized_layers):
        if handle_data.get("error"):
            errors.append(str(handle_data["error"]))
            continue
        handle, rows = context.query.load(handle_data["handle_id"])
        binding = panel.bindings[index] if index < len(panel.bindings) else None
        field_bindings = binding.field_bindings if binding else {}
        compatibility = evaluate_panel(panel.panel_type, handle.kind, rows, field_bindings)
        if panel.panel_type == "comparison" and not compatibility.compatible:
            compatibility = evaluate_panel("kpi", handle.kind, rows, field_bindings)
        if not compatibility.compatible:
            errors.append(compatibility.message)
            continue
        source_key, source_label = _binding_source_identity(binding, handle, rows)
        layers.append(
            _PanelLayer(
                handle=handle,
                binding=binding,
                rows=rows,
                source_key=source_key,
                source_label=source_label,
                color=_map_source_color(source_key),
                warning=str(handle_data["warning"]) if handle_data.get("warning") else None,
            )
        )
    return layers, errors


def _multi_panel_legend(layers: list[_PanelLayer]) -> Any:
    unique = {layer.source_key: layer for layer in layers}
    return html.Div(
        className="talk2d-series-legend",
        children=[
            html.Span(
                className="talk2d-series-legend__item",
                children=[
                    html.I(style={"backgroundColor": layer.color}),
                    html.Span(layer.source_label),
                ],
            )
            for layer in unique.values()
        ],
    )


def _build_multi_binding_panel(
    panel: PanelSpec,
    context: RendererContext,
    materialized: dict[str, dict],
):
    layers, errors = _load_panel_layers(panel, context, materialized)
    if not layers:
        message = errors[0] if errors else "Geen bruikbare databindings voor dit panel."
        return _panel_frame(panel, html.Div(message, className="talk2d-empty"))

    handles = [layer.handle for layer in layers]
    warnings = [layer.warning for layer in layers if layer.warning]
    warning = " ".join(dict.fromkeys([*warnings, *errors])) or None
    record_count = sum(len(layer.rows) for layer in layers)
    source_count = len({layer.source_key for layer in layers})
    meta = f"{record_count} records · {source_count} bronnen"

    def frame(body: Any, *, title: str | None = None):
        display_panel = panel.model_copy(update={"title": title}) if title else panel
        return _panel_frame(
            display_panel,
            body,
            handles[0],
            warning,
            handle_ids=[handle.handle_id for handle in handles],
            meta_override=meta,
        )

    if panel.panel_type in {"event_table", "incident_timeline", "change_summary", "ai_brief"}:
        rows = sorted(
            (row for layer in layers for row in layer.rows),
            key=lambda row: str(row.get("observed_at") or row.get("created_at") or ""),
            reverse=True,
        )
        title = "Live meldingen" if panel.panel_type == "incident_timeline" else None
        return frame(html.Div([_multi_panel_legend(layers), _feed_list(rows)]), title=title)

    if panel.panel_type == "timeseries":
        figure = go.Figure()
        trace_count = 0
        for layer in layers:
            fields = layer.binding.field_bindings
            x_field = fields.get("x", "observed_at")
            y_field = fields.get("y", "value")
            series_field = fields.get("series")
            grouped: dict[str, list[tuple[object, float]]] = {}
            for row in layer.rows:
                x_value = _nested_value(row, x_field)
                y_value = _nested_value(row, y_field)
                if x_value is None or not isinstance(y_value, (int, float)):
                    continue
                location = row.get("location") or {}
                explicit = _nested_value(row, series_field) if series_field else None
                label = str(
                    explicit
                    or (location.get("label") if isinstance(location, dict) else None)
                    or _metric_label(row.get("metric"))
                )
                grouped.setdefault(label, []).append((x_value, float(y_value)))
            for label, points in sorted(grouped.items()):
                ordered = sorted(points, key=lambda point: str(point[0]))
                trace_label = f"{layer.source_label} · {label}"
                figure.add_trace(
                    go.Scatter(
                        name=trace_label,
                        x=[point[0] for point in ordered],
                        y=[point[1] for point in ordered],
                        mode="lines+markers",
                        line={"color": layer.color, "width": 3, "dash": "solid"},
                        marker={"size": 6, "color": layer.color},
                        hovertemplate=f"{trace_label}<br>%{{x}}<br>%{{y:.1f}}<extra></extra>",
                    )
                )
                trace_count += 1
        layout = _figure_layout()
        layout.update(
            {
                "showlegend": trace_count > 1,
                "legend": {"orientation": "h", "y": -0.22, "font": {"size": 10}},
            }
        )
        figure.update_layout(**layout)
        return frame(
            dcc.Graph(
                figure=figure,
                config={"displayModeBar": False, "responsive": True},
                style={"height": "100%"},
            )
        )

    if panel.panel_type == "ranking":
        entries: list[tuple[float, str, _PanelLayer, str]] = []
        for layer in layers:
            fields = layer.binding.field_bindings
            y_field = fields.get("y", "value")
            label_field = fields.get("label", "location.label")
            for row in layer.rows:
                value = _nested_value(row, y_field)
                if not isinstance(value, (int, float)):
                    continue
                label = str(
                    _nested_value(row, label_field)
                    or row.get("title")
                    or row.get("group")
                    or row.get("metric")
                    or "Record"
                )
                entries.append((float(value), label, layer, str(row.get("unit") or "")))
        ranked = sorted(entries, key=lambda item: item[0], reverse=True)[:12]
        ranked.reverse()
        figure = go.Figure(
            go.Bar(
                x=[entry[0] for entry in ranked],
                y=[f"{entry[1]} · {entry[2].source_label}" for entry in ranked],
                orientation="h",
                marker={
                    "color": [entry[2].color for entry in ranked],
                    "line": {"color": "#ffffff", "width": 1},
                },
                customdata=[[entry[2].source_label, entry[3]] for entry in ranked],
                hovertemplate="%{y}<br>%{x:.1f} %{customdata[1]}<extra></extra>",
            )
        )
        layout = _figure_layout()
        layout.update(
            {
                "margin": {"l": 150, "r": 20, "t": 8, "b": 34},
                "yaxis": {"gridcolor": "rgba(0,0,0,0)", "zeroline": False},
            }
        )
        figure.update_layout(**layout)
        return frame(
            html.Div(
                [
                    _multi_panel_legend(layers),
                    dcc.Graph(
                        figure=figure,
                        config={"displayModeBar": False, "responsive": True},
                        style={"height": "calc(100% - 28px)"},
                    ),
                ],
                className="talk2d-composite-panel",
            )
        )

    if panel.panel_type == "comparison":
        labels: list[str] = []
        values: list[float] = []
        colors: list[str] = []
        for layer in layers:
            if layer.handle.kind == "baseline":
                row = layer.rows[0]
                for label, value in (
                    ("Actueel", row.get("current")),
                    ("Normaal", row.get("baseline")),
                ):
                    if isinstance(value, (int, float)):
                        labels.append(f"{layer.source_label} · {label}")
                        values.append(float(value))
                        colors.append(layer.color)
                continue
            for row in layer.rows:
                value = row.get("value")
                if isinstance(value, (int, float)):
                    labels.append(
                        f"{layer.source_label} · {row.get('group') or row.get('metric') or 'Waarde'}"
                    )
                    values.append(float(value))
                    colors.append(layer.color)
        figure = go.Figure(
            go.Bar(
                x=labels,
                y=values,
                marker={"color": colors},
                hovertemplate="%{x}<br>%{y:.1f}<extra></extra>",
            )
        )
        figure.update_layout(**_figure_layout())
        return frame(
            dcc.Graph(
                figure=figure,
                config={"displayModeBar": False, "responsive": True},
                style={"height": "100%"},
            )
        )

    if panel.panel_type == "kpi":
        tiles = []
        for layer in layers:
            row = layer.rows[0]
            value = row.get("current") if layer.handle.kind == "baseline" else row.get("value")
            unit = str(panel.props.get("unit") or row.get("unit") or "")
            value_label = (
                f"{value:g} {unit}".strip() if isinstance(value, (int, float)) else str(value)
            )
            context_label = (
                _metric_label(row.get("metric"))
                if row.get("metric")
                else str(row.get("group") or layer.handle.summary)
            )
            tiles.append(
                html.Div(
                    className="talk2d-kpi talk2d-kpi--source",
                    children=[
                        html.Small(
                            [
                                html.I(style={"backgroundColor": layer.color}),
                                layer.source_label,
                            ]
                        ),
                        html.Strong(value_label),
                        html.Span(context_label),
                    ],
                )
            )
        return frame(html.Div(tiles, className="talk2d-kpi-grid"))

    rows = [dict(row, _talk2d_source=layer.source_label) for layer in layers for row in layer.rows]
    if panel.panel_type == "nearby_places":
        rows.sort(key=lambda row: float(row.get("distance_m") or math.inf))
        columns = [
            {"field": key, "headerName": key.replace("_", " ").title()}
            for key in list(rows[0])[:6]
            if not isinstance(rows[0].get(key), dict)
        ]
        return frame(
            dag.AgGrid(
                rowData=rows,
                columnDefs=columns,
                dashGridOptions={"domLayout": "normal"},
                style={"height": "100%"},
            )
        )
    if panel.panel_type == "evidence":
        counts = Counter(row["_talk2d_source"] for row in rows)
        labels = list(counts)
        values = [counts[label] for label in labels]
        colors = [
            next(layer.color for layer in layers if layer.source_label == label) for label in labels
        ]
        figure = go.Figure(
            go.Pie(
                labels=labels,
                values=values,
                hole=0.62,
                sort=False,
                textinfo="percent",
                marker={"colors": colors},
                hovertemplate="%{label}<br>%{value} records<extra></extra>",
            )
        )
        layout = _figure_layout()
        layout.update({"showlegend": True, "legend": {"orientation": "h", "y": -0.08}})
        figure.update_layout(**layout)
        return frame(
            dcc.Graph(
                figure=figure,
                config={"displayModeBar": False, "responsive": True},
                style={"height": "100%"},
            ),
            title="Bronmix",
        )
    return frame(html.Div("Deze combinatie heeft geen renderer.", className="talk2d-empty"))


def _map_point(
    row: dict[str, Any],
    *,
    latitude: object,
    longitude: object,
    color: str,
    layer_label: str,
) -> dict[str, Any]:
    source = _source_from_row(row)
    source_ref = (
        f"{source.get('stream_id')}:{source.get('record_id')}"
        if source.get("stream_id") and source.get("record_id")
        else None
    )
    description = (
        _feed_description(row, source)
        if row.get("category") or row.get("title") or row.get("description")
        else f"Actuele meting: {_metric_label(row.get('metric'))}."
    )
    meta = " · ".join(
        value
        for value in (
            _source_short_name(str(source.get("owner") or "Bron")),
            _display_time(row.get("observed_at")),
        )
        if value
    )
    return {
        "lat": latitude,
        "lng": longitude,
        "title": _measurement_title(row),
        "description": description,
        "meta": meta,
        "sourceRef": source_ref,
        "streamId": source.get("stream_id"),
        "record_id": source.get("record_id"),
        "color": color,
        "layer": layer_label,
    }


def _panel_materialized_layers(
    panel_id: str, materialized: dict[str, dict]
) -> list[dict[str, Any]]:
    layers = []
    if panel_id in materialized:
        layers.append(materialized[panel_id])
    for layer_index in range(1, 6):
        key = f"{panel_id}::layer:{layer_index}"
        if key in materialized:
            layers.append(materialized[key])
    return layers


def _feed_list(rows: list[dict[str, Any]]) -> Any:
    items = []
    for row in rows[:12]:
        source = _source_from_row(row)
        location = row.get("location") or {}
        latitude = location.get("latitude") if isinstance(location, dict) else None
        longitude = location.get("longitude") if isinstance(location, dict) else None
        source_ref = (
            f"{source.get('stream_id')}:{source.get('record_id')}"
            if source.get("stream_id") and source.get("record_id")
            else None
        )
        items.append(
            _data_li(
                className="talk2d-feed-item",
                **{"data-category": str(row.get("category") or row.get("metric") or "record")},
                children=[
                    html.Div(
                        className="talk2d-feed-meta",
                        children=[
                            html.Time(_display_time(row.get("observed_at"))),
                            html.Span(_source_short_name(str(source.get("owner") or "Bron"))),
                        ],
                    ),
                    html.Div(
                        className="talk2d-feed-copy",
                        children=[
                            html.Strong(
                                row.get("title")
                                or row.get("summary")
                                or row.get("metric")
                                or "Melding zonder titel",
                                className="talk2d-feed-title",
                            ),
                            html.P(
                                _feed_description(row, source),
                                className="talk2d-feed-description",
                            ),
                            html.Small(
                                f"Record {source.get('record_id')}",
                                className="talk2d-feed-record",
                            )
                            if source.get("record_id")
                            else None,
                        ],
                    ),
                    _data_button(
                        "i",
                        className="talk2d-evidence-ref",
                        title="Herkomst bekijken",
                        **{
                            "aria-label": "Herkomst bekijken",
                            "data-source-ref": source_ref,
                        },
                    )
                    if source_ref
                    else None,
                    _data_button(
                        "Focus",
                        className="talk2d-context-ref",
                        title="Gebruik deze melding als context voor het gesprek",
                        **{
                            "aria-label": "Als gespreksfocus gebruiken",
                            "data-context-source-ref": source_ref,
                            "data-context-stream-id": source.get("stream_id"),
                            "data-context-record-id": source.get("record_id"),
                            "data-context-title": (
                                row.get("title")
                                or row.get("summary")
                                or row.get("metric")
                                or "Bronrecord"
                            ),
                            "data-context-description": _feed_description(row, source),
                            "data-context-latitude": latitude,
                            "data-context-longitude": longitude,
                        },
                    )
                    if source_ref
                    else None,
                ],
            )
        )
    return html.Ol(items, className="talk2d-list")


def _build_map_panel(panel, context: RendererContext, materialized: dict[str, dict]):
    layer_data = _panel_materialized_layers(panel.panel_id, materialized)
    if not layer_data:
        return _panel_frame(
            panel, html.Div("Geen geldige databinding voor deze kaart.", className="talk2d-empty")
        )

    points: list[dict[str, Any]] = []
    legend: dict[str, dict[str, Any]] = {}
    handles = []
    warnings: list[str] = []
    errors: list[str] = []
    for layer_index, handle_data in enumerate(layer_data):
        if handle_data.get("error"):
            errors.append(str(handle_data["error"]))
            continue
        handle, rows = context.query.load(handle_data["handle_id"])
        binding = panel.bindings[layer_index] if layer_index < len(panel.bindings) else None
        field_bindings = binding.field_bindings if binding else {}
        compatibility = evaluate_panel(panel.panel_type, handle.kind, rows, field_bindings)
        if not compatibility.compatible:
            errors.append(compatibility.message)
            continue
        handles.append(handle)
        if handle_data.get("warning"):
            warnings.append(str(handle_data["warning"]))
        latitude_field = field_bindings.get("latitude", "location.latitude")
        longitude_field = field_bindings.get("longitude", "location.longitude")
        for row in rows:
            latitude = _nested_value(row, latitude_field)
            longitude = _nested_value(row, longitude_field)
            if latitude is None or longitude is None:
                continue
            source_key = _map_source_key(row, handle.kind)
            source_label = _map_source_label(row, handle.kind)
            color = _map_source_color(source_key)
            points.append(
                _map_point(
                    row,
                    latitude=latitude,
                    longitude=longitude,
                    color=color,
                    layer_label=source_label,
                )
            )
            entry = legend.setdefault(
                source_key, {"label": source_label, "color": color, "count": 0}
            )
            entry["count"] += 1

    if not points:
        message = errors[0] if errors else "Geen records met betrouwbare coordinaten."
        return _panel_frame(panel, html.Div(message, className="talk2d-empty"))

    google_host = html.Div(
        className="talk2d-google-map",
        **{
            "data-map-mode": "3d" if panel.panel_type == "map_3d_google" else "2d",
            "data-points": json.dumps(points, ensure_ascii=True),
            "data-panel-id": panel.panel_id,
        },
        children=[html.Div("Google Maps laden…", className="talk2d-map-loading")],
    )
    figure = go.Figure()
    for entry in legend.values():
        layer_points = [point for point in points if point["layer"] == entry["label"]]
        figure.add_trace(
            go.Scattermap(
                lat=[point["lat"] for point in layer_points],
                lon=[point["lng"] for point in layer_points],
                text=[point["title"] for point in layer_points],
                marker={"size": 13, "color": entry["color"]},
                mode="markers",
                name=entry["label"],
            )
        )
    center = {
        "lat": sum(float(point["lat"]) for point in points) / len(points),
        "lon": sum(float(point["lng"]) for point in points) / len(points),
    }
    figure.update_layout(
        map={"style": "carto-positron", "center": center, "zoom": 7},
        **_figure_layout(),
    )
    legend_node = _data_div(
        className="talk2d-map-legend",
        **{"aria-label": "Kaartlagen"},
        children=[
            html.Div(
                className="talk2d-map-legend__item",
                children=[
                    html.Span(
                        className="talk2d-map-legend__swatch",
                        style={"backgroundColor": entry["color"]},
                    ),
                    html.Span(entry["label"]),
                    html.Small(str(entry["count"])),
                ],
            )
            for entry in legend.values()
        ],
    )
    handle_ids = [handle.handle_id for handle in handles]
    record_count = sum(entry["count"] for entry in legend.values())
    source_count = len(legend)
    warning = " ".join(dict.fromkeys([*warnings, *errors])) or None
    return _panel_frame(
        panel,
        html.Div(
            [
                google_host,
                legend_node,
                html.Div(
                    dcc.Graph(
                        figure=figure,
                        config={"displayModeBar": False, "responsive": True},
                        style={"height": "100%"},
                    ),
                    className="talk2d-map-fallback",
                ),
            ],
            className="talk2d-map-stack",
        ),
        handles[0] if handles else None,
        warning,
        handle_ids=handle_ids,
        meta_override=f"{record_count} records · {source_count} bronnen",
    )


def _build_panel(panel, context: RendererContext, materialized: dict[str, dict]):
    if panel.panel_type == "source_health":
        health = context.sources.health()
        maximum = max((math.log1p(item.record_count) for item in health), default=1.0)
        body = html.Div(
            className="talk2d-health-grid",
            children=[
                _data_div(
                    className="talk2d-health-item",
                    **{"data-status": item.status},
                    children=[
                        html.Div(
                            className="talk2d-health-label",
                            children=[
                                html.Span(className="talk2d-health-dot"),
                                html.Strong(_source_short_name(item.owner), title=item.owner),
                                html.Span(
                                    _source_status_label(item.status),
                                    className="talk2d-source-chip talk2d-source-chip--status",
                                ),
                            ],
                        ),
                        html.Div(
                            className="talk2d-health-volume",
                            children=[
                                html.Span(
                                    html.I(
                                        style={
                                            "width": f"{max(6.0, math.log1p(item.record_count) / maximum * 100):.1f}%"
                                        }
                                    ),
                                    className="talk2d-health-meter",
                                ),
                                html.Span(
                                    item.message
                                    or (
                                        f"{item.record_count} records"
                                        if item.record_count
                                        else "Geen bruikbare records"
                                    ),
                                    className="talk2d-source-chip",
                                ),
                            ],
                        ),
                    ],
                )
                for item in health
            ],
        )
        return _panel_frame(panel.model_copy(update={"title": "Live bronnen"}), body, warning=None)
    if panel.panel_type in {"map_2d", "map_3d_google"}:
        return _build_map_panel(panel, context, materialized)
    if len(_panel_materialized_layers(panel.panel_id, materialized)) > 1:
        return _build_multi_binding_panel(panel, context, materialized)
    handle_data = materialized.get(panel.panel_id)
    if not handle_data:
        return _panel_frame(
            panel, html.Div("Geen geldige databinding voor dit panel.", className="talk2d-empty")
        )
    if handle_data.get("error"):
        return _panel_frame(panel, html.Div(handle_data["error"], className="talk2d-error"))
    handle, rows = context.query.load(handle_data["handle_id"])
    if not rows:
        body = html.Div("Geen records binnen het actieve filter.", className="talk2d-empty")
        return _panel_frame(
            panel,
            body,
            handle,
            handle_data.get("warning"),
        )
    field_bindings = panel.binding.field_bindings if panel.binding else {}
    compatibility = evaluate_panel(panel.panel_type, handle.kind, rows, field_bindings)
    if not compatibility.compatible:
        return _panel_frame(
            panel,
            html.Div(
                className="talk2d-insufficient-series",
                children=[
                    html.Strong("Deze data past niet bij deze weergave."),
                    html.P(compatibility.message),
                ],
            ),
            handle,
            handle_data.get("warning"),
        )
    if panel.panel_type == "ranking":
        y_field = panel.binding.field_bindings.get("y", "value") if panel.binding else "value"
        label_field = (
            panel.binding.field_bindings.get("label", "location.label")
            if panel.binding
            else "location.label"
        )

        def numeric(row: dict) -> float:
            value = _nested_value(row, y_field)
            return float(value) if isinstance(value, (int, float)) else 0.0

        ranked = sorted(
            rows,
            key=numeric,
            reverse=True,
        )[:12]
        ranked.reverse()
        values = [_nested_value(row, y_field) for row in ranked]
        labels = [
            str(
                _nested_value(row, label_field)
                or row.get("title")
                or row.get("group")
                or row.get("metric")
                or "Record"
            )
            for row in ranked
        ]
        unit = str(panel.props.get("unit") or rows[0].get("unit") or "")
        figure = go.Figure(
            go.Bar(
                x=values,
                y=labels,
                orientation="h",
                marker={
                    "color": ["#0878be"] * max(0, len(ranked) - 1) + ["#ffb612"],
                    "line": {"color": "#ffffff", "width": 1},
                },
                hovertemplate=f"%{{y}}<br>%{{x:.1f}} {unit}<extra></extra>",
            )
        )
        layout = _figure_layout()
        layout.update(
            {
                "margin": {"l": 112, "r": 20, "t": 8, "b": 34},
                "xaxis": {
                    "gridcolor": "#e4e9ed",
                    "zeroline": False,
                    "title": {"text": unit},
                },
                "yaxis": {"gridcolor": "rgba(0,0,0,0)", "zeroline": False},
            }
        )
        figure.update_layout(**layout)
        return _panel_frame(
            panel,
            dcc.Graph(
                figure=figure,
                config={"displayModeBar": False, "responsive": True},
                style={"height": "100%"},
            ),
            handle,
            handle_data.get("warning"),
        )
    if panel.panel_type == "timeseries":
        x_field = (
            panel.binding.field_bindings.get("x", "observed_at") if panel.binding else "observed_at"
        )
        y_field = panel.binding.field_bindings.get("y", "value") if panel.binding else "value"
        series_field = panel.binding.field_bindings.get("series") if panel.binding else None

        def series_label(row: dict[str, Any]) -> str:
            if series_field and (explicit := _nested_value(row, series_field)) is not None:
                return str(explicit)
            location = row.get("location") or {}
            location_label = location.get("label") if isinstance(location, dict) else None
            return " · ".join(
                item
                for item in (str(location_label or "Locatie"), _metric_label(row.get("metric")))
                if item
            )

        grouped: dict[str, list[tuple[object, float]]] = {}
        for row in rows:
            x_value = _nested_value(row, x_field)
            y_value = _nested_value(row, y_field)
            if x_value is None or not isinstance(y_value, (int, float)):
                continue
            grouped.setdefault(series_label(row), []).append((x_value, float(y_value)))
        colours = [
            "#0878be",
            "#e85d3f",
            "#00a6a6",
            "#ffb612",
            "#486581",
            "#7a9e2c",
            "#8a5ea7",
            "#d1495b",
        ]
        figure = go.Figure()
        for index, (label, points) in enumerate(sorted(grouped.items())):
            ordered = sorted(points, key=lambda point: str(point[0]))
            figure.add_trace(
                go.Scatter(
                    name=label,
                    x=[point[0] for point in ordered],
                    y=[point[1] for point in ordered],
                    mode="lines+markers",
                    line={"color": colours[index % len(colours)], "width": 3},
                    marker={"size": 6},
                    hovertemplate=f"{label}<br>%{{x}}<br>%{{y:.1f}}<extra></extra>",
                )
            )
        layout = _figure_layout()
        layout["showlegend"] = len(grouped) > 1
        if len(grouped) > 1:
            layout["legend"] = {"orientation": "h", "y": -0.22, "font": {"size": 10}}
        figure.update_layout(**layout)
        return _panel_frame(
            panel,
            dcc.Graph(
                figure=figure,
                config={"displayModeBar": False, "responsive": True},
                style={"height": "100%"},
            ),
            handle,
            handle_data.get("warning"),
        )
    if panel.panel_type == "comparison":
        if handle.kind == "baseline":
            row = rows[0]
            labels = ["Actueel", "Normaal"]
            values = [row.get("current"), row.get("baseline")]
            subtitle = f"Verschil {row.get('delta', 0):+.1f} · z-score {row.get('z_score', 0):+.2f}"
        else:
            labels = [str(row.get("group") or "Groep") for row in rows]
            values = [row.get("value") for row in rows]
            subtitle = f"{len(rows)} deterministische groepen"
        figure = go.Figure(
            go.Bar(
                x=labels,
                y=values,
                marker={"color": ["#0878be", "#ffb612", "#00a6a6", "#e85d3f"][: len(values)]},
                hovertemplate="%{x}<br>%{y:.1f}<extra></extra>",
            )
        )
        layout = _figure_layout()
        layout["annotations"] = [
            {
                "text": subtitle,
                "x": 1,
                "y": 1.08,
                "xref": "paper",
                "yref": "paper",
                "xanchor": "right",
                "showarrow": False,
                "font": {"size": 10, "color": "#5f6f7c"},
            }
        ]
        figure.update_layout(**layout)
        return _panel_frame(
            panel,
            dcc.Graph(
                figure=figure,
                config={"displayModeBar": False, "responsive": True},
                style={"height": "100%"},
            ),
            handle,
            handle_data.get("warning"),
        )
    if panel.panel_type == "correlation":
        row = rows[0]
        coefficient = float(row["correlation"])
        figure = go.Figure(
            go.Indicator(
                mode="number+gauge",
                value=coefficient,
                number={"valueformat": ".2f", "font": {"size": 38}},
                gauge={
                    "shape": "bullet",
                    "axis": {"range": [-1, 1]},
                    "bar": {"color": "#0878be"},
                    "steps": [
                        {"range": [-1, -0.5], "color": "#dcecf5"},
                        {"range": [-0.5, 0.5], "color": "#f3f5f6"},
                        {"range": [0.5, 1], "color": "#e7f2e9"},
                    ],
                },
                title={
                    "text": f"n = {row.get('sample_size', 0)} · samenhang, geen causaliteit",
                    "font": {"size": 13},
                },
            )
        )
        figure.update_layout(**_figure_layout())
        return _panel_frame(
            panel,
            dcc.Graph(
                figure=figure,
                config={"displayModeBar": False, "responsive": True},
                style={"height": "100%"},
            ),
            handle,
            handle_data.get("warning"),
        )
    if panel.panel_type == "kpi":
        row = rows[0]
        value = row.get("current") if handle.kind == "baseline" else row.get("value")
        unit = str(panel.props.get("unit") or row.get("unit") or "")
        value_label = f"{value:g} {unit}".strip() if isinstance(value, (int, float)) else str(value)
        context_label = (
            _metric_label(row.get("metric"))
            if row.get("metric")
            else str(row.get("group") or handle.summary)
        )
        return _panel_frame(
            panel,
            html.Div(
                className="talk2d-kpi",
                children=[html.Strong(value_label), html.Span(context_label)],
            ),
            handle,
            handle_data.get("warning"),
        )
    if panel.panel_type == "event_table":
        return _panel_frame(
            panel,
            _feed_list(rows),
            handle,
            handle_data.get("warning"),
        )
    if panel.panel_type == "nearby_places":
        columns = [
            {"field": key, "headerName": key.replace("_", " ").title()}
            for key in list(rows[0])[:6]
            if not isinstance(rows[0].get(key), dict)
        ]
        return _panel_frame(
            panel,
            dag.AgGrid(
                rowData=rows,
                columnDefs=columns,
                dashGridOptions={"domLayout": "normal"},
                style={"height": "100%"},
            ),
            handle,
            handle_data.get("warning"),
        )
    if panel.panel_type == "evidence":
        counts = Counter(
            _source_short_name(str(_source_from_row(row).get("owner") or "Onbekend"))
            for row in rows
        )
        labels = [name for name, _ in counts.most_common()]
        values = [counts[name] for name in labels]
        figure = go.Figure(
            go.Pie(
                labels=labels,
                values=values,
                hole=0.62,
                sort=False,
                textinfo="percent",
                hovertemplate="%{label}<br>%{value} meldingen<extra></extra>",
                marker={
                    "colors": [
                        "#0878be",
                        "#00a6a6",
                        "#ffb612",
                        "#e85d3f",
                        "#486581",
                        "#7a9e2c",
                        "#8a5ea7",
                    ]
                },
            )
        )
        layout = _figure_layout()
        layout.update(
            {
                "margin": {"l": 8, "r": 8, "t": 8, "b": 8},
                "showlegend": True,
                "legend": {"orientation": "h", "y": -0.08, "x": 0, "font": {"size": 10}},
                "annotations": [
                    {
                        "text": f"<b>{sum(values)}</b><br>meldingen",
                        "showarrow": False,
                        "font": {"size": 13, "color": "#263746"},
                    }
                ],
            }
        )
        figure.update_layout(**layout)
        return _panel_frame(
            panel.model_copy(update={"title": "Bronmix"}),
            dcc.Graph(
                figure=figure,
                config={"displayModeBar": False, "responsive": True},
                style={"height": "100%"},
            ),
            handle,
            handle_data.get("warning"),
        )
    if panel.panel_type in {"incident_timeline", "change_summary", "ai_brief"}:
        return _panel_frame(
            panel.model_copy(
                update={"title": "Live meldingen"}
                if panel.panel_type == "incident_timeline"
                else {}
            ),
            _feed_list(rows),
            handle,
            handle_data.get("warning"),
        )
    value = rows[-1].get("value", handle.row_count)
    return _panel_frame(
        panel,
        html.Div(
            className="talk2d-kpi", children=[html.Strong(str(value)), html.Span(handle.summary)]
        ),
        handle,
        handle_data.get("warning"),
    )


def create_renderer(context: RendererContext, assets_folder: Path) -> Vizro:
    dashboard_model = vm.Dashboard(  # pyright: ignore[reportCallIssue]
        title="Talk2Dashboard NL",
        theme="vizro_light",
        pages=[
            vm.Page(  # pyright: ignore[reportCallIssue]
                title="Operationeel beeld", components=[OperationalShell(id="operational-shell")]
            )
        ],
    )
    vizro = Vizro(assets_folder=str(assets_folder)).build(dashboard_model)
    vizro.dash.config.suppress_callback_exceptions = True

    @callback(
        Output("panel-host", "children"),
        Output("talk2d-dashboard-title", "children"),
        Output("dashboard-subtitle", "children"),
        Output("source-status", "children"),
        Output("version-status", "children"),
        Output("render-context", "children"),
        Input("dashboard-event-store", "data"),
        Input("page-location", "search"),
    )
    def render_dashboard(event, search):
        query = parse_qs((search or "").lstrip("?"))
        capture_version = query.get("dashboard_version", [None])[0]
        spec = (
            context.dashboard.get_version(int(capture_version))
            if capture_version
            else context.dashboard.current()
        )
        assert spec is not None
        bundle = context.sources.latest_bundle_version() or spec.created_from_source_bundle_version
        if (
            isinstance(event, dict)
            and event.get("type") == "source_bundle"
            and spec.refresh_focus in {"manual", "paused"}
        ):
            runtime = context.dashboard.runtime_state()
            if runtime:
                bundle = runtime["source_bundle_version"]
        materialized = context.dashboard.materialize(spec, bundle)
        visible_specs = [
            panel for panel in spec.panels if panel.panel_type not in {"source_health", "evidence"}
        ][:MAX_VISIBLE_PANELS]
        slot_by_panel = _control_room_slots(visible_specs, spec.layout_template)
        render_specs = [
            panel.model_copy(
                update={
                    "props": {
                        **panel.props,
                        "_layout_slot": slot_by_panel.get(panel.panel_id, "support"),
                    }
                }
            )
            for panel in visible_specs
        ]
        rendered_panels = {
            panel.panel_id: _build_panel(panel, context, materialized) for panel in render_specs
        }
        primary = [
            rendered_panels[panel.panel_id]
            for panel in render_specs
            if slot_by_panel.get(panel.panel_id) == "primary"
        ]
        left = [
            rendered_panels[panel.panel_id]
            for panel in render_specs
            if slot_by_panel.get(panel.panel_id, "").startswith("left-")
        ]
        right = [
            rendered_panels[panel.panel_id]
            for panel in render_specs
            if slot_by_panel.get(panel.panel_id, "").startswith("right-")
        ]
        visible_panel_count = max(1, len(visible_specs))
        grid_class = f"talk2d-grid talk2d-grid--count-{visible_panel_count}"
        panel_grid = _data_div(
            [
                _data_div(
                    left,
                    className="talk2d-control-rail talk2d-control-rail--left",
                    style={"gridTemplateRows": f"repeat({max(1, len(left))}, minmax(0, 1fr))"},
                    **{"data-panel-count": len(left)},
                ),
                _data_div(primary, className="talk2d-control-primary"),
                _data_div(
                    right,
                    className="talk2d-control-rail talk2d-control-rail--right",
                    style={"gridTemplateRows": f"repeat({max(1, len(right))}, minmax(0, 1fr))"},
                    **{"data-panel-count": len(right)},
                ),
            ],
            className=grid_class,
            **{
                "data-layout-template": spec.layout_template,
                "data-layout-count": len(visible_specs),
            },
        )
        health = context.sources.health()
        available = sum(
            item.status in {"healthy", "stale", "fixture"} and item.record_count > 0
            for item in health
        )
        last_success = max(
            (item.last_success_at for item in health if item.last_success_at),
            default=None,
        )
        render_context = {
            "dashboard_version": spec.version,
            "source_bundle_version": bundle,
            "handle_ids": [
                layer["handle_id"]
                for panel in visible_specs
                for layer in _panel_materialized_layers(panel.panel_id, materialized)
                if layer.get("handle_id")
            ],
            "panel_ids": [panel.panel_id for panel in visible_specs],
        }
        return (
            panel_grid,
            spec.title,
            spec.subtitle or "Dashboardstate via gevalideerde tools",
            f"{available}/{len(health)} bronnen beschikbaar",
            f"Bijgewerkt {_display_time(last_success)}",
            json.dumps(render_context, ensure_ascii=True),
        )

    return vizro
