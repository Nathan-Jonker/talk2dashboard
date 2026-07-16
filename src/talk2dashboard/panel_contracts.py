from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from talk2dashboard.agent_catalog import PANEL_HANDLE_KINDS

DATA_PANEL_TYPES = (
    "kpi",
    "timeseries",
    "ranking",
    "comparison",
    "incident_timeline",
    "event_table",
    "evidence",
    "map_2d",
    "map_3d_google",
    "nearby_places",
    "correlation",
    "change_summary",
    "ai_brief",
)


@dataclass(frozen=True)
class DataProfile:
    row_count: int
    numeric_value_count: int
    labelled_row_count: int
    distinct_labels: int
    coordinate_count: int
    distinct_timestamps: int
    distinct_metrics: int
    distinct_units: int
    series_count: int
    series_with_history: int
    max_points_per_series: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class PanelCompatibility:
    compatible: bool
    code: str
    message: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def nested_value(row: dict[str, Any], field: str) -> Any:
    value: Any = row
    for part in field.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def row_label(row: dict[str, Any], field: str | None = None) -> str | None:
    if field:
        value = nested_value(row, field)
        return str(value) if value not in {None, ""} else None
    location = row.get("location") or {}
    candidates = (
        row.get("title"),
        row.get("group"),
        location.get("label") if isinstance(location, dict) else None,
    )
    return next((str(candidate) for candidate in candidates if candidate not in {None, ""}), None)


def _series_key(row: dict[str, Any]) -> tuple[str, str]:
    location = row.get("location") or {}
    location_key = (
        location.get("location_id") or location.get("label") if isinstance(location, dict) else None
    )
    return str(location_key or row.get("record_id") or "zonder-locatie"), str(
        row.get("metric") or "zonder-metric"
    )


def profile_rows(rows: list[dict[str, Any]], bindings: dict[str, str] | None = None) -> DataProfile:
    bindings = bindings or {}
    value_field = bindings.get("y", "value")
    label_field = bindings.get("label")
    latitude_field = bindings.get("latitude", "location.latitude")
    longitude_field = bindings.get("longitude", "location.longitude")
    time_field = bindings.get("x") or bindings.get("time") or "observed_at"
    series_sizes: Counter[tuple[str, str]] = Counter()
    timestamps: set[str] = set()
    metrics: set[str] = set()
    units: set[str] = set()
    numeric_count = label_count = coordinate_count = 0
    labels: set[str] = set()
    for row in rows:
        numeric_count += isinstance(nested_value(row, value_field), (int, float))
        label = row_label(row, label_field)
        label_count += label is not None
        if label is not None:
            labels.add(label)
        latitude = nested_value(row, latitude_field)
        longitude = nested_value(row, longitude_field)
        coordinate_count += isinstance(latitude, (int, float)) and isinstance(
            longitude, (int, float)
        )
        timestamp = nested_value(row, time_field)
        if timestamp is not None:
            timestamps.add(str(timestamp))
        if row.get("metric"):
            metrics.add(str(row["metric"]))
        if row.get("unit"):
            units.add(str(row["unit"]))
        if timestamp is not None and isinstance(nested_value(row, value_field), (int, float)):
            series_sizes[_series_key(row)] += 1
    return DataProfile(
        row_count=len(rows),
        numeric_value_count=numeric_count,
        labelled_row_count=label_count,
        distinct_labels=len(labels),
        coordinate_count=coordinate_count,
        distinct_timestamps=len(timestamps),
        distinct_metrics=len(metrics),
        distinct_units=len(units),
        series_count=len(series_sizes),
        series_with_history=sum(size >= 2 for size in series_sizes.values()),
        max_points_per_series=max(series_sizes.values(), default=0),
    )


def _result(
    compatible: bool,
    code: str,
    message: str,
    profile: DataProfile,
    **details: Any,
) -> PanelCompatibility:
    return PanelCompatibility(
        compatible=compatible,
        code=code,
        message=message,
        details={"profile": profile.as_dict(), **details},
    )


def evaluate_panel(
    panel_type: str,
    handle_kind: str,
    rows: list[dict[str, Any]],
    bindings: dict[str, str] | None = None,
) -> PanelCompatibility:
    profile = profile_rows(rows, bindings)
    allowed_kinds = PANEL_HANDLE_KINDS.get(panel_type, ())
    if allowed_kinds and handle_kind not in allowed_kinds:
        return _result(
            False,
            "INCOMPATIBLE_PANEL_BINDING",
            f"{panel_type} ondersteunt geen handle van type {handle_kind}.",
            profile,
            allowed_handle_kinds=list(allowed_kinds),
        )
    if not rows:
        return _result(False, "EMPTY_RESULT", "Het resultaat bevat geen rijen.", profile)
    if panel_type in {"map_2d", "map_3d_google"}:
        if profile.coordinate_count == 0:
            return _result(
                False,
                "MISSING_COORDINATES",
                "Geen enkel record bevat bruikbare coördinaten.",
                profile,
            )
        return _result(
            True,
            "COMPATIBLE",
            "Records met coördinaten kunnen op de kaart worden getoond.",
            profile,
            omitted_rows=profile.row_count - profile.coordinate_count,
        )
    if panel_type == "timeseries":
        if {str(row.get("stream_id")) for row in rows} == {"rws_water"}:
            return _result(
                False,
                "INSUFFICIENT_SERIES",
                "RWS DDAPI20 is een actuele momentopname en levert geen historische meetreeks.",
                profile,
                history_mode="current_snapshot",
            )
        if profile.numeric_value_count != profile.row_count:
            return _result(
                False, "NON_NUMERIC_SERIES", "Niet alle meetpunten zijn numeriek.", profile
            )
        if profile.max_points_per_series < 2:
            return _result(
                False,
                "INSUFFICIENT_SERIES",
                "Geen station-metriekcombinatie bevat twee meetmomenten.",
                profile,
                required_points_per_series=2,
            )
        if profile.series_with_history > 8:
            return _result(
                False,
                "TOO_MANY_SERIES",
                "Filter eerst op locatie of beperk de vergelijking tot maximaal acht reeksen.",
                profile,
                maximum_series=8,
            )
        return _result(True, "COMPATIBLE", "De data bevat een bruikbare tijdreeks.", profile)
    if panel_type == "ranking":
        if profile.row_count < 2 or profile.numeric_value_count != profile.row_count:
            return _result(
                False,
                "INSUFFICIENT_RANKING_DATA",
                "Een ranglijst vereist minstens twee volledig numerieke rijen.",
                profile,
            )
        if profile.labelled_row_count != profile.row_count:
            return _result(
                False, "MISSING_LABELS", "Niet alle ranglijstrijen hebben een label.", profile
            )
        if handle_kind == "series" and (profile.distinct_metrics > 1 or profile.distinct_units > 1):
            return _result(
                False,
                "MIXED_MEASURES",
                "Filter een ranglijst op precies een metriek en eenheid.",
                profile,
            )
        if profile.distinct_labels < 2:
            return _result(
                False,
                "NON_DISTINCT_LABELS",
                "Een ranglijst vereist minstens twee verschillende labels.",
                profile,
            )
        return _result(True, "COMPATIBLE", "De rijen zijn onderling rangschikbaar.", profile)
    if panel_type == "kpi":
        if handle_kind == "baseline":
            return _result(True, "COMPATIBLE", "De baseline bevat een actuele KPI-waarde.", profile)
        if profile.row_count != 1 or profile.numeric_value_count != 1:
            return _result(
                False,
                "KPI_REQUIRES_SINGLE_VALUE",
                "Maak eerst een aggregatie of beperk het resultaat tot een waarde.",
                profile,
            )
        return _result(True, "COMPATIBLE", "Het resultaat bevat een numerieke KPI-waarde.", profile)
    if panel_type == "comparison":
        if handle_kind == "baseline":
            return _result(
                True, "COMPATIBLE", "Actueel en baseline kunnen worden vergeleken.", profile
            )
        if handle_kind != "aggregate" or profile.row_count < 2:
            return _result(
                False,
                "COMPARISON_REQUIRES_GROUPS",
                "Vergelijkingen vereisen minstens twee aggregatiegroepen of een baseline.",
                profile,
            )
        if profile.numeric_value_count != profile.row_count:
            return _result(
                False, "NON_NUMERIC_COMPARISON", "Niet alle groepen zijn numeriek.", profile
            )
        return _result(True, "COMPATIBLE", "De aggregatiegroepen zijn vergelijkbaar.", profile)
    if panel_type == "correlation":
        valid = all(
            isinstance(row.get("correlation"), (int, float))
            and isinstance(row.get("sample_size"), int)
            for row in rows
        )
        return _result(
            valid,
            "COMPATIBLE" if valid else "INVALID_CORRELATION_RESULT",
            "De correlatie-uitvoer is bruikbaar."
            if valid
            else "De handle bevat geen geldige correlatie en steekproefomvang.",
            profile,
        )
    if panel_type in {"event_table", "incident_timeline"}:
        if handle_kind not in {"events", "incident"} or profile.labelled_row_count == 0:
            return _result(
                False, "INVALID_EVENT_FEED", "De handle bevat geen toonbare meldingen.", profile
            )
        return _result(True, "COMPATIBLE", "De meldingen kunnen als feed worden getoond.", profile)
    if panel_type == "nearby_places":
        return _result(
            handle_kind == "places",
            "COMPATIBLE" if handle_kind == "places" else "PLACES_HANDLE_REQUIRED",
            "De voorzieningen kunnen als lijst worden getoond."
            if handle_kind == "places"
            else "Dit panel vereist een Places-handle.",
            profile,
        )
    if panel_type == "change_summary":
        return _result(
            handle_kind == "diff",
            "COMPATIBLE" if handle_kind == "diff" else "DIFF_HANDLE_REQUIRED",
            "De wijzigingsset kan worden samengevat."
            if handle_kind == "diff"
            else "Dit panel vereist een diff-handle.",
            profile,
        )
    return _result(True, "COMPATIBLE", "De handle is bruikbaar voor dit panel.", profile)


def compatibility_summary(handle_kind: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluations = {
        panel_type: evaluate_panel(panel_type, handle_kind, rows) for panel_type in DATA_PANEL_TYPES
    }
    recommended = [
        panel_type
        for panel_type, result in evaluations.items()
        if result.compatible
        and panel_type not in {"evidence", "ai_brief"}
    ]
    if handle_kind == "web_results" and evaluations["evidence"].compatible:
        # Web search is context, not an operational feed. Evidence is hidden from
        # the main panel host and remains available through the provenance UI.
        recommended = ["evidence"]
    blocked = {
        panel_type: result.code
        for panel_type, result in evaluations.items()
        if not result.compatible
        and panel_type in {"kpi", "timeseries", "ranking", "comparison", "map_2d", "correlation"}
    }
    return {
        "profile": profile_rows(rows).as_dict(),
        "recommended_panels": recommended,
        "blocked_panels": blocked,
    }
