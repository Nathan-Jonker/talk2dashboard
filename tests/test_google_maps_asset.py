from __future__ import annotations

from pathlib import Path

ASSET = Path(__file__).resolve().parents[1] / "src/talk2dashboard/renderer/assets/google-maps.js"
RENDERER = Path(__file__).resolve().parents[1] / "src/talk2dashboard/renderer/app.py"
TOKENS = Path(__file__).resolve().parents[1] / "tokens.css"


def test_3d_markers_hide_persistent_titles_and_reuse_click_popover() -> None:
    source = ASSET.read_text()
    mount3d = source.split("async function mount3d", 1)[1].split("async function mount(host)", 1)[0]

    assert 'title: String(point.title || "Bronrecord")' not in mount3d
    assert 'marker.setAttribute("aria-label"' in mount3d
    assert "popover.positionAnchor = marker" in mount3d
    assert "popover.replaceChildren(markerContent(point))" in mount3d
    assert "map.append(popover)" in mount3d


def test_map_evidence_action_dispatches_drawer_event_directly() -> None:
    source = ASSET.read_text()

    assert 'evidence.addEventListener("click"' in source
    assert 'new CustomEvent("talk2d:open-evidence"' in source


def test_map_points_can_be_selected_as_conversation_context() -> None:
    source = ASSET.read_text()

    assert "focus.dataset.contextSourceRef = String(point.sourceRef)" in source
    assert '"Focus actief" : "Als focus gebruiken"' in source
    assert 'new CustomEvent("talk2d:select-context"' in source
    assert "source_ref: String(point.sourceRef)" in source
    assert "record_id: String(point.record_id" in source


def test_selected_map_record_keeps_a_visible_marker_and_record_identity() -> None:
    source = ASSET.read_text()
    renderer = RENDERER.read_text()

    assert 'window.addEventListener("talk2d:context-changed"' in source
    context_changed = source.split('window.addEventListener("talk2d:context-changed"', 1)[1]
    context_changed = context_changed.split("});", 1)[0]
    assert "updateFocusBeacons" in context_changed
    assert '"talk2d-map-marker--focused"' in source
    assert 'record.className = "talk2d-map-popover__record"' in source
    assert 'className="talk2d-feed-record"' in renderer
    assert 'classList.add("talk2d-map-focus-beacon")' in source
    assert "mapFocusTargets.add(focusTarget)" in source
    assert '"data-context-latitude": latitude' in renderer
    assert '"data-context-longitude": longitude' in renderer


def test_2d_and_3d_markers_use_source_layer_colors() -> None:
    source = ASSET.read_text()
    mount2d = source.split("async function mount2d", 1)[1].split("async function mount3d", 1)[0]
    mount3d = source.split("async function mount3d", 1)[1].split("async function mount(host)", 1)[0]

    assert "PinElement" in mount2d
    assert 'background: String(point.color || "#e75b43")' in mount2d
    assert "glyphColor: markerInk(point.color)" in mount2d
    assert "content: pin.element" in mount2d
    assert "PinElement" in mount3d
    assert 'background: String(point.color || "#e75b43")' in mount3d
    assert "glyphColor: markerInk(point.color)" in mount3d
    assert "registerMarkerFocus(point, pin.element)" in mount3d
    assert "marker.append(pin)" in mount3d


def test_source_marker_palette_matches_control_room_tokens() -> None:
    renderer = RENDERER.read_text()
    tokens = TOKENS.read_text()
    palette = {
        "p2000": ("p2000", "#e75b43"),
        "ndw": ("ndw_incidents", "#0878be"),
        "ns": ("ns_disruptions", "#f7d417"),
        "knmi": ("knmi_observations", "#00a6a6"),
        "rws": ("rws_water", "#003b5c"),
        "luchtmeetnet": ("luchtmeetnet", "#2f855a"),
    }

    for token_name, (stream_id, color) in palette.items():
        assert f'"{stream_id}": "{color}"' in renderer
        assert f"--color-source-{token_name}: {color};" in tokens
