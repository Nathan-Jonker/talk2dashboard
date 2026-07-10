from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import parse_qs

import dash_ag_grid as dag
import plotly.graph_objects as go
import vizro.models as vm
from dash import Input, Output, callback, dcc, html
from vizro import Vizro

from talk2dashboard.dashboard import DashboardService
from talk2dashboard.query import QueryEngine
from talk2dashboard.sources.service import SourceService

_data_div = cast(Any, html.Div)


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
                                    "dashboard laden",
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
                        html.Span("live / fixture expliciet gelabeld", className="talk2d-status"),
                    ],
                ),
                html.Main(
                    className="talk2d-main",
                    children=[html.Div(id="panel-host", className="talk2d-grid")],
                ),
                _data_div(id="voice-dock-root", **{"data-agent-mode": "elevenlabs"}),
            ],
        )


vm.Page.add_type("components", OperationalShell)


def _panel_frame(panel, body, handle=None, warning: str | None = None):
    meta = "workspace state"
    if handle:
        newest = handle.freshness.get("newest")
        newest_text = str(newest)[:16].replace("T", " ") if newest else "geen tijd"
        meta = f"{handle.row_count} records · {newest_text} · {handle.source_bundle_version[-8:]}"
    return html.Section(
        id={"type": "dashboard-panel", "index": panel.panel_id},
        className="talk2d-panel",
        **{"data-span": panel.span, "data-panel-type": panel.panel_type},
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
        "margin": {"l": 46, "r": 16, "t": 12, "b": 42},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"family": "Source Sans 3", "color": "#27353a"},
        "xaxis": {"gridcolor": "#dce4e7", "zeroline": False},
        "yaxis": {"gridcolor": "#dce4e7", "zeroline": False},
        "showlegend": False,
        "uirevision": "stable",
    }


def _build_panel(panel, context: RendererContext, materialized: dict[str, dict]):
    if panel.panel_type == "source_health":
        health = context.sources.health()
        body = html.Div(
            className="talk2d-health-grid",
            children=[
                _data_div(
                    className="talk2d-health-item",
                    **{"data-status": item.status},
                    children=[
                        html.Strong(item.owner),
                        html.Span(item.status, className="talk2d-source-chip"),
                        html.Span(
                            item.message or f"{item.record_count} records",
                            className="talk2d-source-chip",
                        ),
                    ],
                )
                for item in health
            ],
        )
        return _panel_frame(panel, body)
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
    if panel.panel_type in {"timeseries", "comparison", "correlation"}:
        x_field = (
            panel.binding.field_bindings.get("x", "observed_at") if panel.binding else "observed_at"
        )
        y_field = panel.binding.field_bindings.get("y", "value") if panel.binding else "value"
        figure = go.Figure(
            go.Scatter(
                x=[row.get(x_field) for row in rows],
                y=[row.get(y_field) for row in rows],
                mode="lines+markers",
                line={"color": "#0066a1", "width": 2},
                marker={"color": "#e85d3f", "size": 6},
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
    if panel.panel_type in {"map_2d", "map_3d_google"}:
        geo = [(row.get("location") or {}) for row in rows]
        points = [
            {
                "lat": item.get("latitude"),
                "lng": item.get("longitude"),
                "title": row.get("title", row.get("metric", "Bronrecord")),
                "record_id": row.get("record_id"),
            }
            for row, item in zip(rows, geo, strict=True)
            if item.get("latitude") is not None and item.get("longitude") is not None
        ]
        google_host = html.Div(
            className="talk2d-google-map",
            **{
                "data-map-mode": "3d" if panel.panel_type == "map_3d_google" else "2d",
                "data-points": json.dumps(points, ensure_ascii=True),
                "data-panel-id": panel.panel_id,
            },
            children=[html.Div("Google Maps laden…", className="talk2d-map-loading")],
        )
        figure = go.Figure(
            go.Scattermap(
                lat=[item.get("latitude") for item in geo],
                lon=[item.get("longitude") for item in geo],
                text=[row.get("title", row.get("metric", "Bronrecord")) for row in rows],
                marker={"size": 13, "color": "#e85d3f"},
                mode="markers",
            )
        )
        figure.update_layout(
            map={"style": "carto-positron", "center": {"lat": 51.75, "lon": 4.65}, "zoom": 7},
            **_figure_layout(),
        )
        return _panel_frame(
            panel,
            html.Div(
                [
                    google_host,
                    html.Div(
                        dcc.Graph(
                            figure=figure,
                            config={"displayModeBar": False, "responsive": True},
                            style={"height": "24rem"},
                        ),
                        className="talk2d-map-fallback",
                    ),
                ],
                className="talk2d-map-stack",
            ),
            handle,
            handle_data.get("warning"),
        )
    if panel.panel_type in {"event_table", "nearby_places"}:
        columns = [
            {"field": key, "headerName": key.replace("_", " ").title()}
            for key in list(rows[0])[:6]
            if not isinstance(rows[0].get(key), dict)
        ]
        return _panel_frame(
            panel,
            dag.AgGrid(
                rowData=rows, columnDefs=columns, dashGridOptions={"domLayout": "autoHeight"}
            ),
            handle,
            handle_data.get("warning"),
        )
    if panel.panel_type in {"incident_timeline", "evidence", "change_summary", "ai_brief"}:
        items = []
        for row in rows[:12]:
            observed = str(row.get("observed_at", ""))[11:16]
            items.append(
                html.Li(
                    [
                        html.Time(observed),
                        html.Span(
                            row.get("title") or row.get("summary") or row.get("metric", "Record")
                        ),
                    ]
                )
            )
        return _panel_frame(
            panel,
            html.Ol(items, className="talk2d-list"),
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
        panels = [_build_panel(panel, context, materialized) for panel in spec.panels]
        healthy = sum(item.status == "healthy" for item in context.sources.health())
        context.dashboard.acknowledge_render(
            spec.version,
            bundle,
            [value["handle_id"] for value in materialized.values()],
            "rendered",
        )
        return (
            panels,
            spec.title,
            spec.subtitle or "Dashboardstate via gevalideerde tools",
            f"{healthy}/{len(context.sources.health())} bronnen gezond",
            f"dashboard v{spec.version} · data {bundle[-8:]}",
        )

    return vizro
