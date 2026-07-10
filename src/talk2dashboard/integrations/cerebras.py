from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx

from talk2dashboard.agent_prompt import VISUAL_QA_PROMPT
from talk2dashboard.config import Settings
from talk2dashboard.integrations.audit import record_provider_call
from talk2dashboard.storage.database import Database


class CerebrasService:
    url = "https://api.cerebras.ai/v1/chat/completions"

    def __init__(self, settings: Settings, database: Database | None = None) -> None:
        self.settings = settings
        self.database = database

    @property
    def configured(self) -> bool:
        return bool(self.settings.cerebras_api_key)

    async def _complete(self, messages: list[dict[str, Any]], max_tokens: int = 500) -> dict:
        if not self.configured:
            raise RuntimeError("CEREBRAS_NOT_CONFIGURED")
        started = time.monotonic_ns()
        try:
            async with httpx.AsyncClient(timeout=40) as client:
                response = await client.post(
                    self.url,
                    headers={
                        "Authorization": f"Bearer {self.settings.cerebras_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.settings.cerebras_model,
                        "messages": messages,
                        "temperature": 0.1,
                        "max_tokens": max_tokens,
                        "response_format": {"type": "json_object"},
                    },
                )
            response.raise_for_status()
            payload = response.json()
            record_provider_call(
                self.database,
                provider="cerebras",
                operation="chat_completion",
                status="success",
                duration_ms=(time.monotonic_ns() - started) / 1_000_000,
                usage=payload.get("usage"),
            )
        except Exception:
            record_provider_call(
                self.database,
                provider="cerebras",
                operation="chat_completion",
                status="error",
                duration_ms=(time.monotonic_ns() - started) / 1_000_000,
            )
            raise
        content = payload["choices"][0]["message"]["content"]
        return json.loads(content)

    async def plan_focus(self, source_summary: list[dict[str, Any]]) -> dict[str, Any]:
        prompt = (
            "Kies een eerste operationele dashboardfocus uit de enums. Retourneer JSON met "
            "layout_template (incident_focus|trend_focus|mobility_focus|environment_focus|evidence_focus|briefing), "
            "title (max 60 tekens), subtitle (max 120 tekens), focus_streams (max 3 geldige stream_ids) "
            "en reason. Geen cijfers verzinnen. Bronnen: "
            + json.dumps(source_summary, ensure_ascii=True, separators=(",", ":"))
        )
        return await self._complete(
            [
                {"role": "system", "content": "Je kiest alleen presentatie, nooit brondata."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=250,
        )

    async def analyze_dashboard(self, image: bytes, structured_state: dict[str, Any]) -> dict:
        image_url = "data:image/png;base64," + base64.b64encode(image).decode()
        return await self._complete(
            [
                {"role": "system", "content": VISUAL_QA_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": json.dumps(structured_state, ensure_ascii=True)},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
            max_tokens=600,
        )
