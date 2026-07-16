from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from uuid import uuid4

from talk2dashboard.agent_catalog import STREAM_IDS
from talk2dashboard.dashboard import DashboardService
from talk2dashboard.domain import ToolRequest
from talk2dashboard.integrations.cerebras import CerebrasService
from talk2dashboard.sources.service import SourceService
from talk2dashboard.tools.executor import ToolExecutor


class InitialDashboardPlanner:
    AUTO_REDESIGN_COOLDOWN: ClassVar[timedelta] = timedelta(minutes=15)
    _layouts: ClassVar[set[str]] = {
        "incident_focus",
        "trend_focus",
        "mobility_focus",
        "environment_focus",
        "evidence_focus",
        "briefing",
    }
    _priority: ClassVar[tuple[str, ...]] = (
        "p2000",
        "ndw_incidents",
        "knmi_observations",
        "rws_water",
        "luchtmeetnet",
        "ns_disruptions",
        "nos_rss",
    )
    _recipes: ClassVar[dict[str, dict[str, Any]]] = {
        "knmi_observations": {
            "query": {
                "operation": "query_measurements",
                "stream": "knmi_observations",
                "metric": "wind_gust_kmh",
                "sort": "value",
                "order": "desc",
                "limit": 12,
            },
            "panel_type": "ranking",
            "title": "Hoogste actuele windstoten",
            "fields": {"label": "location.label", "y": "value"},
            "props": {"unit": "km/h"},
        },
        "rws_water": {
            "query": {
                "operation": "query_measurements",
                "stream": "rws_water",
                "metric": "water_level_cm",
                "sort": "value",
                "order": "desc",
                "limit": 12,
            },
            "panel_type": "ranking",
            "title": "Actuele waterstanden",
            "fields": {"label": "location.label", "y": "value"},
            "props": {"unit": "cm"},
        },
        "luchtmeetnet": {
            "query": {
                "operation": "query_measurements",
                "stream": "luchtmeetnet",
                "metric": "no2_ug_m3",
                "sort": "value",
                "order": "desc",
                "limit": 12,
            },
            "panel_type": "ranking",
            "title": "Hoogste actuele NO2-metingen",
            "fields": {"label": "location.label", "y": "value"},
            "props": {"unit": "µg/m³"},
        },
        "ndw_incidents": {
            "query": {
                "operation": "query_events",
                "stream": "ndw_incidents",
                "window": "PT6H",
                "order": "desc",
                "limit": 40,
            },
            "panel_type": "map_3d_google",
            "title": "Actuele verkeerssituaties",
            "fields": {
                "latitude": "location.latitude",
                "longitude": "location.longitude",
            },
            "props": {},
        },
        "p2000": {
            "query": {
                "operation": "query_events",
                "stream": "p2000",
                "window": "PT6H",
                "order": "desc",
                "limit": 40,
            },
            "panel_type": "incident_timeline",
            "title": "Recente hulpverleningssignalen",
            "fields": {"time": "observed_at", "label": "title"},
            "props": {},
        },
        "ns_disruptions": {
            "query": {
                "operation": "query_events",
                "stream": "ns_disruptions",
                "window": "PT24H",
                "order": "desc",
                "limit": 30,
            },
            "panel_type": "event_table",
            "title": "Actuele spoorverstoringen",
            "fields": {"time": "observed_at", "label": "title"},
            "props": {},
        },
        "nos_rss": {
            "query": {
                "operation": "query_events",
                "stream": "nos_rss",
                "window": "P2D",
                "order": "desc",
                "limit": 20,
            },
            "panel_type": "event_table",
            "title": "Actuele nieuwscontext",
            "fields": {"time": "observed_at", "label": "title"},
            "props": {},
        },
    }

    def __init__(
        self,
        cerebras: CerebrasService,
        sources: SourceService,
        dashboard: DashboardService,
        tools: ToolExecutor,
    ) -> None:
        self.cerebras = cerebras
        self.sources = sources
        self.dashboard = dashboard
        self.tools = tools
        self._lock = asyncio.Lock()

    def cooldown_status(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = self.dashboard.current(optional=True)
        if current is None:
            return {
                "eligible": False,
                "reason": "dashboard_missing",
                "last_dashboard_update_at": None,
                "next_automatic_at": None,
                "cooldown_minutes": int(self.AUTO_REDESIGN_COOLDOWN.total_seconds() / 60),
            }

        checked_at = now or datetime.now(UTC)
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=UTC)
        last_update = current.created_at
        if last_update.tzinfo is None:
            last_update = last_update.replace(tzinfo=UTC)
        next_automatic_at = last_update + self.AUTO_REDESIGN_COOLDOWN
        initial_dashboard = current.version == 1 and current.created_by == "system"
        eligible = initial_dashboard or checked_at >= next_automatic_at
        reason = (
            "initial_dashboard"
            if initial_dashboard
            else "cooldown_elapsed"
            if eligible
            else "cooldown_active"
        )
        return {
            "eligible": eligible,
            "reason": reason,
            "last_dashboard_update_at": last_update.isoformat(),
            "next_automatic_at": None if initial_dashboard else next_automatic_at.isoformat(),
            "cooldown_minutes": int(self.AUTO_REDESIGN_COOLDOWN.total_seconds() / 60),
        }

    async def initialize(self, *, force: bool = False, now: datetime | None = None) -> bool:
        async with self._lock:
            if not self.cerebras.configured:
                return False
            current = self.dashboard.current(optional=True)
            bundle = self.sources.latest_bundle_version()
            if current is None or bundle is None:
                return False
            if not force and not self.cooldown_status(now=now)["eligible"]:
                return False

            health = self.sources.health()
            source_summary = [
                {
                    "stream_id": item.stream_id,
                    "status": item.status,
                    "record_count": item.record_count,
                    "newest_record_at": (
                        item.newest_record_at.isoformat() if item.newest_record_at else None
                    ),
                }
                for item in health
            ]
            proposal = await self.cerebras.plan_focus(source_summary)
            available = {
                item.stream_id
                for item in health
                if item.record_count > 0 and str(item.status) not in {"offline", "disabled"}
            }
            requested = proposal.get("focus_streams")
            selected = (
                [
                    stream_id
                    for stream_id in requested
                    if stream_id in available and stream_id in STREAM_IDS
                ]
                if isinstance(requested, list)
                else []
            )
            selected.extend(
                stream_id
                for stream_id in self._priority
                if stream_id in available and stream_id not in selected
            )
            selected = selected[:3]
            if not selected:
                return False

            policy_version = int(self.tools.policy()["version"])
            data_operations = []
            for stream_id in selected:
                operation = dict(self._recipes[stream_id]["query"])
                operation["save_as"] = stream_id
                data_operations.append(operation)
            data_result = await self.tools.execute(
                "data_batch",
                ToolRequest(
                    request_id=f"startup-data-{uuid4().hex}",
                    session_policy_version=policy_version,
                    dashboard_version=current.version,
                    payload={"operations": data_operations},
                ),
            )
            if not data_result.ok or not isinstance(data_result.result, dict):
                raise RuntimeError(f"Startup data composition failed: {data_result.error}")

            aliases = data_result.result["aliases"]
            results_by_alias = {
                str(item.get("alias")): item
                for item in data_result.result.get("results", [])
                if item.get("alias")
            }
            panel_operations: list[dict[str, Any]] = []
            for order, stream_id in enumerate(selected, start=1):
                recipe = self._recipes[stream_id]
                result_item = results_by_alias.get(stream_id, {})
                recommended = list(
                    (result_item.get("panel_compatibility") or {}).get("recommended_panels", [])
                )
                panel_type = str(recipe["panel_type"])
                fields = dict(recipe["fields"])
                if panel_type not in recommended:
                    panel_type = next(
                        (
                            candidate
                            for candidate in (
                                "event_table",
                                "incident_timeline",
                                "ranking",
                                "map_3d_google",
                                "map_2d",
                                "kpi",
                                "comparison",
                            )
                            if candidate in recommended
                        ),
                        "",
                    )
                    fields = {
                        "event_table": {"time": "observed_at", "label": "title"},
                        "incident_timeline": {"time": "observed_at", "label": "title"},
                        "ranking": {"label": "location.label", "y": "value"},
                        "map_2d": {
                            "latitude": "location.latitude",
                            "longitude": "location.longitude",
                        },
                        "map_3d_google": {
                            "latitude": "location.latitude",
                            "longitude": "location.longitude",
                        },
                        "kpi": {"y": "value"},
                        "comparison": {"label": "group", "y": "value"},
                    }.get(panel_type, {})
                if not panel_type:
                    continue
                panel_operations.append(
                    {
                        "op": "upsert_panel",
                        "panel_id": f"startup-{stream_id.replace('_', '-')}",
                        "panel_type": panel_type,
                        "title": recipe["title"],
                        "seed_handle_id": aliases[stream_id],
                        "binding": {
                            "seed_handle_id": aliases[stream_id],
                            "field_bindings": fields,
                        },
                        "span": "standard",
                        "order": order,
                        "props": recipe["props"],
                    }
                )
            layout = proposal.get("layout_template")
            if layout not in self._layouts:
                layout = "incident_focus"
            title = str(proposal.get("title") or current.title)[:60]
            subtitle = str(proposal.get("subtitle") or current.subtitle or "")[:120]
            operations: list[dict[str, Any]] = [
                {"op": "set_layout_template", "values": {"layout_template": layout}},
                {"op": "set_meta", "values": {"title": title, "subtitle": subtitle}},
                *panel_operations,
            ]
            dashboard_result = await self.tools.execute(
                "dashboard_batch",
                ToolRequest(
                    request_id=f"startup-dashboard-{uuid4().hex}",
                    session_policy_version=policy_version,
                    dashboard_version=current.version,
                    payload={
                        "expected_version": current.version,
                        "composition_mode": "replace_visible",
                        "reason": (
                            ("manual" if force else "automatic")
                            + " Cerebras composition: "
                            + str(proposal.get("reason") or "actuele bronfocus")[:160]
                        ),
                        "operations": operations,
                    },
                ),
            )
            if not dashboard_result.ok:
                raise RuntimeError(
                    f"Startup dashboard composition failed: {dashboard_result.error}"
                )
            return True
