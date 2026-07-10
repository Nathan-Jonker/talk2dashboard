from __future__ import annotations

import json

import httpx
import respx

from talk2dashboard.integrations.cerebras import CerebrasService
from talk2dashboard.integrations.elevenlabs_config import client_tool, desired_agent_config
from talk2dashboard.tools.definitions import TOOL_DEFINITIONS


def test_elevenlabs_agent_config_is_dutch_normalized_and_blocking(services) -> None:
    settings, *_ = services
    config = desired_agent_config(settings, ["tool-1"])["conversation_config"]
    assert config["agent"]["language"] == "nl"
    assert config["agent"]["first_message"] == ""
    assert config["tts"]["model_id"] == "eleven_flash_v2_5"
    assert config["tts"]["text_normalisation_type"] == "elevenlabs"
    assert config["conversation"]["max_duration_seconds"] == settings.max_conversation_seconds
    tools = [client_tool(definition) for definition in TOOL_DEFINITIONS]
    assert len(tools) == 6
    assert all(tool["type"] == "client" and tool["expects_response"] for tool in tools)


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
