from __future__ import annotations

from typing import Any

from talk2dashboard.agent_prompt import SYSTEM_PROMPT
from talk2dashboard.config import Settings


def schema_for_elevenlabs(schema: dict[str, Any], fallback: str = "Waarde") -> dict[str, Any]:
    kind = schema.get("type", "string")
    output: dict[str, Any] = {
        "type": kind,
        "description": schema.get("description", fallback),
    }
    if "enum" in schema:
        output["enum"] = schema["enum"]
    if kind == "array":
        output["items"] = schema_for_elevenlabs(schema.get("items", {}), "Lijstwaarde")
    if kind == "object":
        output["properties"] = {
            name: schema_for_elevenlabs(value, f"Waarde voor {name}")
            for name, value in schema.get("properties", {}).items()
        }
        output["required"] = schema.get("required", [])
    return output


def client_tool(definition: dict[str, Any]) -> dict[str, Any]:
    parameters = schema_for_elevenlabs(definition["parameters"], "Toolparameters")
    return {
        "type": "client",
        "name": definition["name"],
        "description": definition["description"],
        "expects_response": True,
        "response_timeout_secs": 30 if definition["name"] == "capture_dashboard" else 15,
        "parameters": parameters,
        "dynamic_variables": {"dynamic_variable_placeholders": {}},
        "assignments": [],
        "interruption_mode": "allow",
        "pre_tool_speech": "auto",
        "tool_call_sound": None,
        "tool_call_sound_behavior": "auto",
        "tool_error_handling_mode": "auto",
        "execution_mode": "immediate",
    }


def desired_agent_config(
    settings: Settings, tool_ids: list[str], current_prompt: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "conversation_config": {
            "asr": {
                "quality": "high",
                "provider": "scribe_realtime",
                "user_input_audio_format": "pcm_16000",
            },
            "turn": {
                "turn_timeout": 7,
                "turn_eagerness": "normal",
                "speculative_turn": False,
            },
            "agent": {
                "first_message": "",
                "language": "nl",
                "prompt": {
                    "prompt": SYSTEM_PROMPT,
                    "llm": settings.elevenlabs_llm_model,
                    "temperature": 0.2,
                    "max_tokens": 450,
                    "tool_ids": tool_ids or (current_prompt or {}).get("tool_ids", []),
                    "enable_parallel_tool_calls": True,
                },
            },
            "tts": {
                "model_id": "eleven_flash_v2_5",
                "voice_id": settings.elevenlabs_voice_id,
                "agent_output_audio_format": "pcm_24000",
                "optimize_streaming_latency": 2,
                "stability": 0.5,
                "speed": 1.0,
                "similarity_boost": 0.8,
                "text_normalisation_type": "elevenlabs",
            },
            "conversation": {
                "max_duration_seconds": settings.max_conversation_seconds,
                "client_events": ["audio", "interruption", "user_transcript", "agent_response"],
            },
        }
    }
