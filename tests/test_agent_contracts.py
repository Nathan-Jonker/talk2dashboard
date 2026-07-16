from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from talk2dashboard.agent_catalog import (
    MEASUREMENT_METRICS,
    PANEL_TYPES,
    STREAM_CAPABILITIES,
    STREAM_IDS,
    TOOL_CAPABILITIES,
)
from talk2dashboard.agent_prompt import SYSTEM_PROMPT
from talk2dashboard.integrations.cerebras import CerebrasService
from talk2dashboard.integrations.elevenlabs_config import (
    client_tool,
    desired_agent_config,
    managed_value_differences,
    managed_values_match,
)
from talk2dashboard.renderer.app import _plain_text
from talk2dashboard.tools.definitions import TOOL_DEFINITIONS

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_elevenlabs_agent_config_is_dutch_normalized_and_blocking(services) -> None:
    settings, *_ = services
    config = desired_agent_config(settings, ["tool-1"])["conversation_config"]
    assert config["agent"]["language"] == "nl"
    assert config["agent"]["first_message"] == ""
    assert config["agent"]["prompt"]["llm"] == "qwen36-35b-a3b"
    assert config["agent"]["prompt"]["enable_parallel_tool_calls"] is True
    assert config["tts"]["model_id"] == "eleven_flash_v2_5"
    assert config["tts"]["text_normalisation_type"] == "elevenlabs"
    assert config["conversation"]["max_duration_seconds"] == settings.max_conversation_seconds
    end_call = config["agent"]["prompt"]["built_in_tools"]["end_call"]
    assert end_call["type"] == "system"
    assert end_call["params"]["system_tool_type"] == "end_call"
    assert config["agent"]["prompt"]["tool_ids"] == ["tool-1"]
    tools = [client_tool(definition) for definition in TOOL_DEFINITIONS]
    assert len(tools) == 6
    assert all(tool["type"] == "client" and tool["expects_response"] for tool in tools)
    assert all(tool["pre_tool_speech"] == "off" for tool in tools)
    assert all(
        definition["display_name"] and definition["category"] for definition in TOOL_DEFINITIONS
    )


def test_agent_config_disables_parallel_calls_for_unsupported_llm(services) -> None:
    settings, *_ = services
    config = desired_agent_config(
        settings,
        [],
        supports_parallel_tool_calls=False,
    )["conversation_config"]

    assert config["agent"]["prompt"]["enable_parallel_tool_calls"] is False


def test_agent_has_compact_routing_catalog_and_explicit_batch_schemas() -> None:
    for stream_id in STREAM_IDS:
        assert stream_id in SYSTEM_PROMPT
    for metric in MEASUREMENT_METRICS:
        assert metric in SYSTEM_PROMPT
    assert "Roep inspect_workspace NIET aan" in SYSTEM_PROMPT
    assert "Een of twee gelijkwaardige feeds" in SYSTEM_PROMPT
    assert "span=full alleen als de gebruiker expliciet" in SYSTEM_PROMPT
    assert "maximaal vijfentwintig gesproken woorden" in SYSTEM_PROMPT
    assert "bevestig in één zin wat zichtbaar is veranderd" in SYSTEM_PROMPT
    assert "Herhaal de vraag niet" in SYSTEM_PROMPT
    assert "kan ik nog ergens mee helpen?" in SYSTEM_PROMPT
    assert "rws_water levert via DDAPI20 WFS alleen de laatste waarneming" in SYSTEM_PROMPT
    assert "Volg panel_compatibility" in SYSTEM_PROMPT
    assert "maximaal acht reeksen" in SYSTEM_PROMPT
    assert "stille operatorcontext" in SYSTEM_PROMPT
    assert "precies dat geselecteerde record" in SYSTEM_PROMPT
    definitions = {item["name"]: item for item in TOOL_DEFINITIONS}
    data_item = definitions["data_batch"]["parameters"]["properties"]["operations"]["items"]
    assert {"operation", "stream", "metric", "sort", "order", "filters"} <= set(
        data_item["properties"]
    )
    assert "enum" not in data_item["properties"]["metric"]
    assert "query_nearby" in data_item["properties"]["operation"]["enum"]
    assert data_item["properties"]["radius_m"]["maximum"] == 10_000
    assert "query_nearby" in SYSTEM_PROMPT
    dashboard = definitions["dashboard_batch"]["parameters"]
    assert "expected_version" not in dashboard["required"]
    dashboard_item = dashboard["properties"]["operations"]["items"]
    assert set(PANEL_TYPES) <= set(dashboard_item["properties"]["panel_type"]["enum"])


def test_operator_capability_catalog_documents_all_public_contracts() -> None:
    tool_names = {str(item["name"]) for item in TOOL_DEFINITIONS}
    assert set(TOOL_CAPABILITIES) == tool_names
    assert set(STREAM_CAPABILITIES) == set(STREAM_IDS)

    for documentation in TOOL_CAPABILITIES.values():
        assert documentation["inputs"]
        assert documentation["outputs"]
        assert documentation["constraints"]
        assert documentation["examples"]

    for stream_id, documentation in STREAM_CAPABILITIES.items():
        assert documentation["display_name"]
        source_inputs = documentation["inputs"]
        assert isinstance(source_inputs, list) and source_inputs
        assert documentation["fields"]
        assert documentation["possibilities"]
        assert documentation["examples"]
        assert documentation["limitations"]
        if stream_id in {"knmi_observations", "rws_water", "luchtmeetnet"}:
            assert documentation["metrics"]
        input_names = {
            str(item["name"]) for item in source_inputs if isinstance(item, dict) and "name" in item
        }
        assert {"operation", "stream", "window", "filters[].field / op / value"} <= input_names

    rws = STREAM_CAPABILITIES["rws_water"]
    possibilities = rws["possibilities"]
    limitations = rws["limitations"]
    data_outputs = TOOL_CAPABILITIES["data_batch"]["outputs"]
    assert isinstance(possibilities, list)
    assert isinstance(limitations, list)
    assert isinstance(data_outputs, list)
    assert "tijdreeks" not in possibilities
    assert any("laatste waarneming" in str(limitation) for limitation in limitations)
    assert any("distinct_timestamps" in str(output) for output in data_outputs)


def test_renderer_has_an_honest_single_measurement_fallback() -> None:
    renderer_source = (PROJECT_ROOT / "src/talk2dashboard/renderer/app.py").read_text()
    dashboard_css = (PROJECT_ROOT / "src/talk2dashboard/renderer/assets/dashboard.css").read_text()
    assert "evaluate_panel(panel.panel_type" in renderer_source
    assert "Deze data past niet bij deze weergave" in renderer_source
    assert ".talk2d-insufficient-series" in dashboard_css


def test_feed_descriptions_are_rendered_as_plain_text() -> None:
    assert _plain_text("<p>Actueel <strong>nieuws</strong> &amp; context.</p>") == (
        "Actueel nieuws & context."
    )


def test_information_drawer_remains_clickable_inside_fixed_voice_root() -> None:
    css = (PROJECT_ROOT / "voice_dock/src/styles.css").read_text()
    dashboard_css = (PROJECT_ROOT / "src/talk2dashboard/renderer/assets/dashboard.css").read_text()
    drawer_source = (PROJECT_ROOT / "voice_dock/src/InfoDrawer.tsx").read_text()
    backdrop = css.split(".info-backdrop {", 1)[1].split("}", 1)[0]
    drawer = css.split(".info-drawer {", 1)[1].split("}", 1)[0]
    assert "pointer-events: auto" in backdrop
    assert "overflow-y: scroll" in drawer
    assert "overscroll-behavior: contain" in drawer
    assert "touch-action: pan-y" in drawer
    assert "--talk2d-info-drawer-width" in css
    assert "--talk2d-info-drawer-width: clamp(24rem, 42vw, 32rem)" in dashboard_css
    assert "calc(100vw - var(--talk2d-info-drawer-width))" in dashboard_css
    assert "html.talk2d-info-open .talk2d-shell" in dashboard_css
    assert "#right-side {\n  zoom: 1 !important;" in dashboard_css
    assert "grid-template-rows: auto minmax(0, 1fr)" in dashboard_css
    assert '[data-layout-count="1"]' in dashboard_css
    assert '[data-layout-count="6"]' in dashboard_css
    assert '[data-layout-count="12"]' in dashboard_css
    assert ".talk2d-control-primary" in dashboard_css
    assert ".talk2d-control-rail--left" in dashboard_css
    renderer_source = (PROJECT_ROOT / "src/talk2dashboard/renderer/app.py").read_text()
    assert '"data-layout-slot"' in renderer_source
    assert '"data-layout-template"' in renderer_source
    assert 'Output("panel-host", "className")' not in renderer_source
    assert 'className="talk2d-panel-host"' in renderer_source
    assert 'classList.toggle("talk2d-info-open", open)' in drawer_source
    assert 'dispatchEvent(new Event("resize"))' in drawer_source


def test_first_render_requests_rate_limited_startup_composition() -> None:
    eventstream = (PROJECT_ROOT / "src/talk2dashboard/renderer/assets/eventstream.js").read_text()
    api_source = (PROJECT_ROOT / "src/talk2dashboard/api/app.py").read_text()
    assert 'eventType === "initial" && !initializationRequested' in eventstream
    assert 'fetch("/api/dashboard/initialize"' in eventstream
    assert '@app.post("/api/dashboard/initialize")' in api_source
    planner_source = (PROJECT_ROOT / "src/talk2dashboard/planning.py").read_text()
    drawer_source = (PROJECT_ROOT / "voice_dock/src/InfoDrawer.tsx").read_text()
    assert "AUTO_REDESIGN_COOLDOWN: ClassVar[timedelta] = timedelta(minutes=15)" in planner_source
    assert "current.created_at" in planner_source
    assert "self._initialized" not in planner_source
    assert "Dashboard nu opnieuw samenstellen" in drawer_source
    assert "JSON.stringify({ force: true })" in drawer_source
    assert "talk2dSetCerebrasGeneration" in drawer_source
    assert "talk2dAwaitDashboardRender" in drawer_source
    assert "await window.talk2dAwaitDashboardRender" in drawer_source
    assert 'new CustomEvent("talk2d:cerebras-generation"' in eventstream
    assert "window.talk2dAwaitDashboardRender = awaitDashboardRender" in eventstream
    assert "return awaitDashboardRender(result.dashboard_version, 20000)" in eventstream
    assert "setCerebrasGeneration(requestId, false" in eventstream


def test_operator_focus_survives_dashboard_rerenders() -> None:
    eventstream = (PROJECT_ROOT / "src/talk2dashboard/renderer/assets/eventstream.js").read_text()

    assert 'const operatorSelectionKey = "talk2d_operator_selection"' in eventstream
    assert "new MutationObserver(scheduleContextFocus)" in eventstream
    assert 'new CustomEvent("talk2d:context-changed"' in eventstream
    assert 'target.setAttribute("aria-pressed"' in eventstream


def test_elevenlabs_model_display_name_is_mapped_to_api_id(services) -> None:
    settings, *_ = services
    configured = settings.model_copy(update={"elevenlabs_llm_model": "Qwen3.6-35B-A3B"})
    config = desired_agent_config(configured, ["tool-1"])["conversation_config"]
    assert config["agent"]["prompt"]["llm"] == "qwen36-35b-a3b"


def test_agent_sync_comparison_ignores_provider_defaults() -> None:
    expected = {"name": "data_batch", "parameters": {"required": []}}
    actual = {
        "name": "data_batch",
        "parameters": {"required": [], "provider_metadata": {"version": 2}},
        "response_timeout_secs": 15,
    }
    assert managed_values_match(expected, actual)
    assert not managed_values_match({"name": "dashboard_batch"}, actual)
    assert managed_value_differences(expected, actual) == []
    assert managed_value_differences({"name": "dashboard_batch"}, actual) == [
        ("conversation_config.name", "dashboard_batch", "data_batch")
    ]


@respx.mock
async def test_cerebras_visual_qa_sends_image_and_records_usage(services) -> None:
    settings, database, *_ = services
    configured = settings.model_copy(update={"cerebras_api_key": "test-key"})
    route = respx.post(CerebrasService.url).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"total_tokens": 42},
            },
        )
    )
    result = await CerebrasService(configured, database).analyze_dashboard(
        b"png-bytes", {"dashboard_version": 3, "handles": ["hdl_1"]}
    )
    body = json.loads(route.calls[0].request.content)
    user_content = body["messages"][1]["content"]
    assert result == {"ok": True}
    assert body["model"] == configured.cerebras_model
    assert user_content[1]["type"] == "image_url"
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")
