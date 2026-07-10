from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

from talk2dashboard.config import get_settings
from talk2dashboard.integrations.elevenlabs_config import client_tool, desired_agent_config
from talk2dashboard.tools.definitions import TOOL_DEFINITIONS

API_ROOT = "https://api.elevenlabs.io"


def safe_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, dict):
        return str(detail.get("status") or detail.get("message") or "request failed")[:300]
    return str(detail)[:300]


def request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> httpx.Response:
    response = client.request(method, f"{API_ROOT}{path}", **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path}: {safe_error(response)}")
    return response


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
            elif current.get("tool_config") != wanted:
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
        end_call = existing.get("end_call")
        if end_call is None:
            changes.append("system tool toevoegen: end_call")
            if args.apply:
                end_call = request(
                    client,
                    "POST",
                    "/v1/convai/tools",
                    json={
                        "tool_config": {
                            "type": "system",
                            "name": "end_call",
                            "params": {"system_tool_type": "end_call"},
                            "description": "Beeindig het gesprek wanneer de gebruiker expliciet klaar is.",
                        }
                    },
                ).json()
        if end_call:
            tool_ids.append(end_call["id"])

        config = agent.get("conversation_config", {})
        current_prompt = config.get("agent", {}).get("prompt", {})
        desired = desired_agent_config(settings, tool_ids, current_prompt)
        comparable = json.dumps(desired["conversation_config"], sort_keys=True)
        current_subset = {
            section: config.get(section, {}) for section in desired["conversation_config"]
        }
        if json.dumps(current_subset, sort_keys=True) != comparable:
            changes.append("agent conversation_config bijwerken")
            if args.apply:
                request(
                    client,
                    "PATCH",
                    f"/v1/convai/agents/{settings.elevenlabs_agent_id}",
                    json=desired,
                )
    if changes:
        print("\n".join(changes))
        return 1 if args.check else 0
    print("ElevenLabs-agent is in sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
