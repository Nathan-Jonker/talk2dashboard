from __future__ import annotations

from talk2dashboard.panel_contracts import compatibility_summary, evaluate_panel


def measurement(
    *,
    record_id: str,
    metric: str = "wind_gust_kmh",
    value: float = 42.0,
    observed_at: str = "2026-07-14T10:00:00Z",
    label: str | None = "IJmuiden",
) -> dict:
    return {
        "record_id": record_id,
        "stream_id": "knmi_observations",
        "metric": metric,
        "value": value,
        "unit": "km/h" if metric == "wind_gust_kmh" else "ug/m3",
        "observed_at": observed_at,
        "location": (
            {
                "location_id": f"station:{label}",
                "label": label,
                "latitude": 52.46,
                "longitude": 4.61,
            }
            if label
            else None
        ),
    }


def test_cross_station_snapshot_is_ranking_and_map_not_timeseries() -> None:
    rows = [
        measurement(record_id="one", label="IJmuiden", value=42),
        measurement(record_id="two", label="De Bilt", value=31),
    ]

    summary = compatibility_summary("series", rows)

    assert "ranking" in summary["recommended_panels"]
    assert "map_2d" in summary["recommended_panels"]
    assert "timeseries" not in summary["recommended_panels"]
    assert summary["blocked_panels"]["timeseries"] == "INSUFFICIENT_SERIES"


def test_single_station_history_is_a_timeseries() -> None:
    rows = [
        measurement(record_id="one", observed_at="2026-07-14T10:00:00Z"),
        measurement(record_id="two", observed_at="2026-07-14T10:10:00Z", value=46),
    ]

    result = evaluate_panel("timeseries", "series", rows)

    assert result.compatible
    assert result.details["profile"]["series_with_history"] == 1


def test_large_multi_station_history_requires_a_location_filter() -> None:
    rows = [
        measurement(
            record_id=f"{station}-{minute}",
            observed_at=f"2026-07-14T10:{minute:02d}:00Z",
            label=f"Station {station}",
        )
        for station in range(9)
        for minute in (0, 10)
    ]

    result = evaluate_panel("timeseries", "series", rows)

    assert not result.compatible
    assert result.code == "TOO_MANY_SERIES"


def test_mixed_metrics_cannot_form_one_ranking() -> None:
    rows = [
        measurement(record_id="wind", metric="wind_gust_kmh"),
        measurement(record_id="air", metric="no2_ug_m3", value=18),
    ]

    result = evaluate_panel("ranking", "series", rows)

    assert not result.compatible
    assert result.code == "MIXED_MEASURES"


def test_event_without_coordinates_is_a_feed_but_not_a_map() -> None:
    rows = [
        {
            "record_id": "nos:1",
            "title": "Nieuwsbericht",
            "observed_at": "2026-07-14T10:00:00Z",
            "location": None,
        }
    ]

    assert evaluate_panel("event_table", "events", rows).compatible
    map_result = evaluate_panel("map_2d", "events", rows)
    assert not map_result.compatible
    assert map_result.code == "MISSING_COORDINATES"


def test_correlation_requires_correlation_payload() -> None:
    valid = [{"correlation": 0.42, "sample_size": 24, "causality": False}]

    assert evaluate_panel("correlation", "correlation", valid).compatible
    assert not evaluate_panel("correlation", "correlation", [{}]).compatible


def test_web_results_recommend_evidence_only() -> None:
    rows = [
        {
            "title": "Verkeersbericht A16",
            "url": "https://example.invalid/a16",
            "snippet": "Onbevestigde externe context over de A16.",
            "provider": "ddgs",
        }
    ]

    summary = compatibility_summary("web_results", rows)

    assert summary["recommended_panels"] == ["evidence"]
    assert evaluate_panel("evidence", "web_results", rows).compatible
    assert not evaluate_panel("event_table", "web_results", rows).compatible
