from __future__ import annotations

import pytest

from talk2dashboard.domain import PanelSpec
from talk2dashboard.renderer.app import _control_room_slots


def panel(panel_id: str, panel_type: str, order: int) -> PanelSpec:
    return PanelSpec.model_validate(
        {
            "panel_id": panel_id,
            "panel_type": panel_type,
            "title": panel_id,
            "order": order,
        }
    )


@pytest.mark.parametrize(
    ("layout_template", "expected_primary"),
    [
        ("incident_focus", "map"),
        ("mobility_focus", "map"),
        ("trend_focus", "trend"),
        ("environment_focus", "trend"),
        ("briefing", "feed"),
        ("evidence_focus", "comparison"),
    ],
)
def test_control_room_primary_panel_follows_layout_template(
    layout_template: str, expected_primary: str
) -> None:
    panels = [
        panel("feed", "incident_timeline", 0),
        panel("map", "map_3d_google", 1),
        panel("trend", "timeseries", 2),
        panel("comparison", "comparison", 3),
    ]

    slots = _control_room_slots(panels, layout_template)

    assert slots[expected_primary] == "primary"


@pytest.mark.parametrize("panel_count", range(1, 13))
def test_control_room_allocates_one_unique_slot_per_visible_panel(panel_count: int) -> None:
    panel_types = [
        "map_3d_google",
        "incident_timeline",
        "kpi",
        "ranking",
        "timeseries",
        "correlation",
        "map_2d",
        "event_table",
        "comparison",
        "nearby_places",
        "change_summary",
        "ai_brief",
    ]
    panels = [panel(f"panel-{index}", panel_types[index], index) for index in range(panel_count)]

    slots = _control_room_slots(panels, "incident_focus")

    assert len(slots) == panel_count
    assert len(set(slots.values())) == panel_count
    assert list(slots.values()).count("primary") == 1
    left_count = sum(slot.startswith("left-") for slot in slots.values())
    right_count = sum(slot.startswith("right-") for slot in slots.values())
    assert left_count <= 5
    assert right_count <= 6
    assert right_count - left_count in {0, 1}


def test_control_room_uses_panel_order_as_tie_breaker() -> None:
    panels = [
        panel("first-map", "map_2d", 1),
        panel("second-map", "map_3d_google", 2),
        panel("feed", "event_table", 3),
    ]

    slots = _control_room_slots(panels, "incident_focus")

    assert slots["first-map"] == "primary"
    assert slots["feed"] == "left-1"
    assert slots["second-map"] == "right-1"


def test_control_room_falls_back_to_first_panel_for_unknown_composition() -> None:
    panels = [panel("places", "nearby_places", 0), panel("kpi", "kpi", 1)]

    slots = _control_room_slots(panels, "unknown")

    assert slots["places"] == "primary"
    assert slots["kpi"] == "right-1"
