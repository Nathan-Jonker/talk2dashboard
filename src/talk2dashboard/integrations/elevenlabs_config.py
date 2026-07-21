from __future__ import annotations

from typing import Any

from talk2dashboard.agent_prompt import SYSTEM_PROMPT
from talk2dashboard.config import Settings


def managed_values_match(expected: Any, actual: Any) -> bool:
    """Compare project-owned values while ignoring provider-added response fields."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(
            managed_values_match(value, actual.get(key))
            if key in actual
            else value in ({}, [], None)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(
                managed_values_match(wanted, current)
                for wanted, current in zip(expected, actual, strict=True)
            )
        )
    return expected == actual


def managed_value_differences(
    expected: Any, actual: Any, path: str = "conversation_config"
) -> list[tuple[str, Any, Any]]:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [(path, expected, actual)]
        differences: list[tuple[str, Any, Any]] = []
        for key, value in expected.items():
            child_path = f"{path}.{key}"
            if key not in actual:
                if value not in ({}, [], None):
                    differences.append((child_path, value, None))
                continue
            differences.extend(managed_value_differences(value, actual[key], child_path))
        return differences
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return [(path, expected, actual)]
        differences: list[tuple[str, Any, Any]] = []
        for index, (wanted, current) in enumerate(zip(expected, actual, strict=True)):
            differences.extend(managed_value_differences(wanted, current, f"{path}[{index}]"))
        return differences
    return [] if expected == actual else [(path, expected, actual)]


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
        "response_timeout_secs": 45 if definition["name"] == "capture_dashboard" else 30,
        "parameters": parameters,
        "dynamic_variables": {"dynamic_variable_placeholders": {}},
        "assignments": [],
        "interruption_mode": "allow",
        # The dashboard already exposes a thinking/tool state. Extra pre-tool speech adds
        # latency and encourages the model to narrate schema discovery instead of acting.
        "pre_tool_speech": "off",
        "tool_call_sound": None,
        "tool_call_sound_behavior": "auto",
        "tool_error_handling_mode": "auto",
        "execution_mode": "immediate",
    }


def desired_agent_config(
    settings: Settings,
    tool_ids: list[str],
    current_prompt: dict[str, Any] | None = None,
    *,
    supports_parallel_tool_calls: bool = True,
) -> dict[str, Any]:
    return {
        "conversation_config": {
            "asr": {
                "quality": "high",
                "provider": "scribe_realtime",
                "user_input_audio_format": "pcm_16000",
            },
            "turn": {
                "turn_timeout": settings.elevenlabs_turn_timeout_seconds,
                "silence_end_call_timeout": settings.elevenlabs_silence_end_call_seconds,
                "turn_eagerness": "normal",
                "speculative_turn": False,
            },
            "agent": {
                "first_message": "",
                "language": "nl",
                "prompt": {
                    "prompt": SYSTEM_PROMPT,
                    "llm": settings.elevenlabs_llm_api_model,
                    "temperature": 0.2,
                    "max_tokens": 450,
                    "tool_ids": tool_ids or (current_prompt or {}).get("tool_ids", []),
                    "built_in_tools": {
                        "end_call": {
                            "type": "system",
                            "name": "end_call",
                            "description": (
                                "Beeindig het gesprek wanneer de gebruiker expliciet klaar is."
                            ),
                            "params": {"system_tool_type": "end_call"},
                        }
                    },
                    "enable_parallel_tool_calls": supports_parallel_tool_calls,
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
