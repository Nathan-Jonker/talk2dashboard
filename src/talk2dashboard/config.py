from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    app_log_level: str = "INFO"
    database_path: Path = ROOT / "data/talk2dashboard.sqlite3"
    asset_dir: Path = ROOT / "data/assets"
    raw_dir: Path = ROOT / "data/raw"
    fixture_dir: Path = ROOT / "data/fixtures"
    data_retention_days: int = Field(default=90, ge=14, le=3650)
    max_conversation_seconds: int = Field(default=900, ge=60, le=3600)
    fixture_only: bool = False

    elevenlabs_api_key: str = ""
    elevenlabs_agent_id: str = "agent_3301kx2t5vbbfsbaezy059s1e8t8"
    elevenlabs_voice_id: str = "SXBL9NbvTrjsJQYay2kT"
    elevenlabs_llm_model: str = "qwen36-35b-a3b"
    elevenlabs_server_location: Literal["us", "eu-residency", "in-residency", "global"] = (
        "global"
    )

    cerebras_api_key: str = ""
    cerebras_model: str = "gemma-4-31b"

    knmi_api_key: str = ""
    ns_api_subscription_key: str = ""
    p2000_provider_api_key: str = ""
    p2000_provider_mode: Literal["auto", "112radar", "rss", "disabled"] = "auto"

    google_maps_browser_api_key: str = ""
    google_geocoding_server_api_key: str = ""
    google_places_server_api_key: str = ""
    google_map_id: str = ""
    places_daily_budget: int = Field(default=100, ge=0, le=10000)
    geocoding_daily_budget: int = Field(default=100, ge=0, le=10000)

    brave_search_api_key: str = ""
    source_refresh_seconds: int = Field(default=60, ge=10, le=3600)
    knmi_station_ids: str = ""
    cluster_join_distance_m: int = Field(default=2000, ge=100, le=100000)
    cluster_join_window_minutes: int = Field(default=30, ge=1, le=1440)
    cluster_active_min_events: int = Field(default=2, ge=1, le=20)
    cluster_resolve_after_minutes: int = Field(default=120, ge=5, le=10080)

    @property
    def root(self) -> Path:
        return ROOT

    @property
    def elevenlabs_llm_api_model(self) -> str:
        aliases = {
            "qwen3.6-35b-a3b": "qwen36-35b-a3b",
            "qwen36-35b-a3b": "qwen36-35b-a3b",
        }
        return aliases.get(self.elevenlabs_llm_model.casefold(), self.elevenlabs_llm_model)

    def ensure_directories(self) -> None:
        for path in (self.database_path.parent, self.asset_dir, self.raw_dir, self.fixture_dir):
            path.mkdir(parents=True, exist_ok=True)

    def public_config(self) -> dict[str, object]:
        return {
            "app": {
                "language": "nl-NL",
                "mode": "fixture-only" if self.fixture_only else "local-single-user",
            },
            "voice": {
                "configured": bool(self.elevenlabs_api_key and self.elevenlabs_agent_id),
                "agent_id": self.elevenlabs_agent_id,
                "voice_id": self.elevenlabs_voice_id,
                "llm_model": self.elevenlabs_llm_api_model,
                "server_location": self.elevenlabs_server_location,
            },
            "cerebras": {"configured": bool(self.cerebras_api_key), "model": self.cerebras_model},
            "maps": {
                "browser_configured": bool(self.google_maps_browser_api_key),
                "places_configured": bool(self.google_places_server_api_key),
                "geocoding_configured": bool(self.google_geocoding_server_api_key),
                "map_id": self.google_map_id or None,
            },
            "search": {
                "configured": True,
                "provider": "brave" if self.brave_search_api_key else "duckduckgo",
                "default_enabled": False,
            },
            "sources": {
                "knmi": bool(self.knmi_api_key),
                "ns": bool(self.ns_api_subscription_key),
                "p2000_112radar": bool(self.p2000_provider_api_key),
                "rws": True,
                "ndw": True,
                "luchtmeetnet": True,
                "nos": True,
                "p2000_rss": True,
            },
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
