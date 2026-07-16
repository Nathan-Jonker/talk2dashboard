from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from talk2dashboard.config import get_settings  # noqa: E402
from talk2dashboard.integrations.elevenlabs_config import (  # noqa: E402
    client_tool,
    desired_agent_config,
    managed_value_differences,
    managed_values_match,
)
from talk2dashboard.tools.definitions import LEGACY_TOOL_NAMES, TOOL_DEFINITIONS  # noqa: E402

API_ROOT = "https://api.elevenlabs.io"


def safe_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, dict):
        summary = {
            key: detail[key]
            for key in ("status", "message", "errors")
            if detail.get(key) is not None
        }
        return json.dumps(summary or detail, ensure_ascii=True)[:1200]
    return str(detail)[:1200]


def request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> httpx.Response:
    response = client.request(method, f"{API_ROOT}{path}", **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path}: {safe_error(response)}")
    return response


def supports_parallel_tool_calls(client: httpx.Client, model: str) -> bool:
    payload = request(client, "GET", "/v1/convai/llm/list").json()
    for item in payload.get("llms", []):
        if item.get("llm") == model:
            return bool(item.get("supports_parallel_tool_calls"))
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Controleer of synchroniseer de Talk2Dashboard-agent."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.elevenlabs_api_key or not settings.elevenlabs_agent_id:
        print("ELEVENLABS_API_KEY of ELEVENLABS_AGENT_ID ontbreekt.", file=sys.stderr)
        return 2
    headers = {"xi-api-key": settings.elevenlabs_api_key, "Content-Type": "application/json"}
    with httpx.Client(timeout=30, headers=headers) as client:
        agent = request(client, "GET", f"/v1/convai/agents/{settings.elevenlabs_agent_id}").json()
        tools_payload = request(client, "GET", "/v1/convai/tools").json()
        existing = {
            item.get("tool_config", {}).get("name"): item
            for item in tools_payload.get("tools", [])
            if item.get("id") and item.get("tool_config", {}).get("name")
        }
        tool_ids: list[str] = []
        changes: list[str] = []
        for definition in TOOL_DEFINITIONS:
            wanted = client_tool(definition)
            current = existing.get(definition["name"])
            if current is None:
                changes.append(f"tool toevoegen: {definition['name']}")
                if args.apply:
                    current = request(
                        client, "POST", "/v1/convai/tools", json={"tool_config": wanted}
                    ).json()
            elif not managed_values_match(wanted, current.get("tool_config")):
                changes.append(f"tool bijwerken: {definition['name']}")
                if args.apply:
                    current = request(
                        client,
                        "PATCH",
                        f"/v1/convai/tools/{current['id']}",
                        json={"tool_config": wanted},
                    ).json()
            if current:
                tool_ids.append(current["id"])
        config = agent.get("conversation_config", {})
        current_prompt = config.get("agent", {}).get("prompt", {})
        parallel_supported = supports_parallel_tool_calls(client, settings.elevenlabs_llm_api_model)
        desired = desired_agent_config(
            settings,
            tool_ids,
            current_prompt,
            supports_parallel_tool_calls=parallel_supported,
        )
        if not parallel_supported:
            print(
                f"LLM {settings.elevenlabs_llm_api_model} ondersteunt geen parallelle "
                "platform-toolcalls; data_batch paralleliseert onafhankelijke reads intern."
            )
        current_subset = {
            section: config.get(section, {}) for section in desired["conversation_config"]
        }
        if not managed_values_match(desired["conversation_config"], current_subset):
            changes.append("agent conversation_config bijwerken")
            if args.check:
                for path, wanted, current in managed_value_differences(
                    desired["conversation_config"], current_subset
                )[:8]:
                    print(
                        f"  drift {path}: verwacht={str(wanted)[:120]!r}, "
                        f"actueel={str(current)[:120]!r}"
                    )
            if args.apply:
                request(
                    client,
                    "PATCH",
                    f"/v1/convai/agents/{settings.elevenlabs_agent_id}",
                    json=desired,
                )
        for name in sorted(LEGACY_TOOL_NAMES):
            legacy = existing.get(name)
            if legacy is None:
                continue
            changes.append(f"legacy tool verwijderen: {name}")
            if args.apply:
                request(client, "DELETE", f"/v1/convai/tools/{legacy['id']}")
    if changes:
        print("\n".join(changes))
        return 1 if args.check else 0
    print("ElevenLabs-agent is in sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
