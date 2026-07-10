from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
from a2wsgi import WSGIMiddleware
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from talk2dashboard.capture import CaptureService
from talk2dashboard.config import Settings, get_settings
from talk2dashboard.dashboard import DashboardService
from talk2dashboard.domain import ToolRequest
from talk2dashboard.integrations.cerebras import CerebrasService
from talk2dashboard.integrations.places import GeocodingClient, PlacesClient
from talk2dashboard.integrations.search import BraveSearchClient
from talk2dashboard.maintenance import MaintenanceService
from talk2dashboard.planning import InitialDashboardPlanner
from talk2dashboard.query import QueryEngine
from talk2dashboard.renderer.app import RendererContext, create_renderer
from talk2dashboard.sources.service import SourceService
from talk2dashboard.storage.assets import AssetStore
from talk2dashboard.storage.database import Database
from talk2dashboard.storage.models import (
    ConversationEventRow,
    ConversationRow,
    LatencyEventRow,
    ProviderCallRow,
    ToolAuditRow,
)
from talk2dashboard.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)


class PolicyUpdate(BaseModel):
    web_search_enabled: bool | None = None
    auto_update_enabled: bool | None = None


class RenderAck(BaseModel):
    dashboard_version: int
    source_bundle_version: str
    handle_ids: list[str] = Field(default_factory=list)
    status: str
    error: str | None = None


class MetricEvent(BaseModel):
    conversation_id: str | None = None
    turn_id: str | None = None
    event_type: str
    monotonic_ms: float
    payload: dict[str, Any] = Field(default_factory=dict)


class SessionRegistration(BaseModel):
    conversation_id: str


class ConversationEvent(BaseModel):
    event_id: str
    conversation_id: str
    turn_id: str | None = None
    role: str
    text: str = Field(max_length=12000)
    final: bool = True


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.ensure_directories()
        self.database = Database(settings.database_path)
        self.database.initialize()
        self.assets = AssetStore(settings.asset_dir, self.database)
        self.maintenance = MaintenanceService(settings, self.database, self.assets)
        self.sources = SourceService(settings, self.database)
        self.query = QueryEngine(self.database)
        self.dashboard = DashboardService(self.database, self.query)
        self.cerebras = CerebrasService(settings, self.database)
        self.capture = CaptureService(settings, self.dashboard, self.assets, self.cerebras)
        self.planner = InitialDashboardPlanner(self.cerebras, self.sources, self.dashboard)
        self.tools = ToolExecutor(
            self.database,
            self.sources,
            self.query,
            self.dashboard,
            PlacesClient(settings, self.database),
            GeocodingClient(settings, self.database),
            BraveSearchClient(settings, self.database),
            self.capture.capture,
        )
        self.background_task: asyncio.Task | None = None
        self.planner_task: asyncio.Task | None = None
        self.maintenance_task: asyncio.Task | None = None


settings = get_settings()
container = Container(settings)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bundle = container.sources.latest_bundle_version()
    if bundle is None or not container.sources.bundle_has_records(bundle):
        bundle = await container.sources.initialize_fixture()
    container.dashboard.ensure_default(bundle)
    if not settings.fixture_only:
        container.background_task = asyncio.create_task(
            container.sources.run(), name="source-refresh"
        )
    container.maintenance_task = asyncio.create_task(
        container.maintenance.run(), name="retention-maintenance"
    )

    async def run_initial_planner() -> None:
        try:
            if await container.planner.initialize():
                current = container.dashboard.current()
                assert current is not None
                await container.sources.publish(
                    {
                        "type": "dashboard_updated",
                        "dashboard_version": current.version,
                    }
                )
        except Exception as exc:
            logger.warning("Initial dashboard planner failed: %s", type(exc).__name__)

    if not settings.fixture_only:
        container.planner_task = asyncio.create_task(
            run_initial_planner(), name="initial-dashboard-planner"
        )
    current = container.dashboard.current()
    assert current is not None
    await container.sources.publish(
        {"type": "dashboard_ready", "dashboard_version": current.version}
    )
    try:
        yield
    finally:
        container.sources.stop()
        if container.background_task:
            container.background_task.cancel()
            await asyncio.gather(container.background_task, return_exceptions=True)
        if container.planner_task:
            container.planner_task.cancel()
            await asyncio.gather(container.planner_task, return_exceptions=True)
        container.maintenance.stop()
        if container.maintenance_task:
            container.maintenance_task.cancel()
            await asyncio.gather(container.maintenance_task, return_exceptions=True)


app = FastAPI(title="Talk2Dashboard NL", version="0.1.0", lifespan=lifespan)


@app.get("/api/config")
def public_config() -> dict[str, Any]:
    config = settings.public_config()
    config["policy"] = container.tools.policy()
    config["source_health"] = [item.model_dump(mode="json") for item in container.sources.health()]
    return config


@app.get("/api/maps/client-config")
def maps_client_config() -> dict[str, Any]:
    """Browser keys are public credentials and must be HTTP-referrer restricted."""
    return {
        "api_key": settings.google_maps_browser_api_key,
        "map_id": settings.google_map_id or None,
        "language": "nl",
        "region": "NL",
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "time": datetime.now(UTC).isoformat()}


@app.post("/api/session/elevenlabs-token")
async def elevenlabs_token() -> dict[str, str]:
    if not settings.elevenlabs_api_key or not settings.elevenlabs_agent_id:
        raise HTTPException(
            503,
            detail={
                "code": "ELEVENLABS_NOT_CONFIGURED",
                "message": "ElevenLabs key of agent-ID ontbreekt.",
            },
        )
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            "https://api.elevenlabs.io/v1/convai/conversation/token",
            params={"agent_id": settings.elevenlabs_agent_id},
            headers={"xi-api-key": settings.elevenlabs_api_key},
        )
    if response.status_code >= 400:
        raise HTTPException(
            response.status_code,
            detail={
                "code": "ELEVENLABS_TOKEN_FAILED",
                "message": "ElevenLabs conversation token kon niet worden opgehaald.",
            },
        )
    token = response.json().get("token")
    if not token:
        raise HTTPException(
            502,
            detail={
                "code": "ELEVENLABS_TOKEN_MISSING",
                "message": "ElevenLabs retourneerde geen conversation token.",
            },
        )
    return {"conversation_token": token}


@app.post("/api/session/elevenlabs-signed-url")
async def elevenlabs_signed_url() -> dict[str, str]:
    if not settings.elevenlabs_api_key or not settings.elevenlabs_agent_id:
        raise HTTPException(503, detail={"code": "ELEVENLABS_NOT_CONFIGURED"})
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
            params={"agent_id": settings.elevenlabs_agent_id},
            headers={"xi-api-key": settings.elevenlabs_api_key},
        )
    if response.status_code >= 400:
        raise HTTPException(
            response.status_code,
            detail={
                "code": "ELEVENLABS_SIGNED_URL_FAILED",
                "message": "ElevenLabs text-session URL kon niet worden opgehaald.",
            },
        )
    signed_url = response.json().get("signed_url")
    if not signed_url:
        raise HTTPException(502, detail={"code": "ELEVENLABS_SIGNED_URL_MISSING"})
    return {"signed_url": signed_url}


@app.post("/api/session/end")
def end_session(payload: dict[str, Any]) -> dict[str, bool]:
    conversation_id = payload.get("conversation_id")
    if conversation_id:
        with container.database.session() as session:
            row = session.get(ConversationRow, conversation_id)
            if row:
                row.status = "ended"
                row.ended_at = datetime.now(UTC).isoformat()
    return {"ok": True}


@app.post("/api/session/register")
def register_session(payload: SessionRegistration) -> dict[str, bool]:
    with container.database.session() as session:
        session.merge(
            ConversationRow(
                conversation_id=payload.conversation_id,
                started_at=datetime.now(UTC).isoformat(),
                ended_at=None,
                status="active",
                model=settings.elevenlabs_llm_model,
                voice_id=settings.elevenlabs_voice_id,
            )
        )
    return {"ok": True}


@app.post("/api/conversations/event")
def conversation_event(payload: ConversationEvent) -> dict[str, bool]:
    current = container.dashboard.current(optional=True)
    runtime = container.dashboard.runtime_state()
    with container.database.session() as session:
        session.merge(
            ConversationEventRow(
                event_id=payload.event_id,
                conversation_id=payload.conversation_id,
                turn_id=payload.turn_id,
                role=payload.role,
                text=payload.text,
                final=payload.final,
                model=settings.elevenlabs_llm_model,
                prompt_version="talk2dashboard-v1",
                dashboard_version=current.version if current else None,
                source_bundle_version=(runtime or {}).get("source_bundle_version"),
                handle_ids_json=json.dumps((runtime or {}).get("handle_ids", [])),
                created_at=datetime.now(UTC).isoformat(),
            )
        )
    return {"ok": True}


@app.get("/api/conversations/events")
def conversation_events(limit: int = 500) -> list[dict[str, Any]]:
    with container.database.session() as session:
        rows = session.scalars(
            select(ConversationEventRow)
            .order_by(ConversationEventRow.created_at.desc())
            .limit(min(max(limit, 1), 2000))
        ).all()
        return [
            {
                "event_id": row.event_id,
                "conversation_id": row.conversation_id,
                "turn_id": row.turn_id,
                "role": row.role,
                "text": row.text,
                "final": row.final,
                "model": row.model,
                "prompt_version": row.prompt_version,
                "dashboard_version": row.dashboard_version,
                "source_bundle_version": row.source_bundle_version,
                "handle_ids": json.loads(row.handle_ids_json),
                "created_at": row.created_at,
            }
            for row in rows
        ]


@app.get("/api/policy")
def get_policy() -> dict[str, Any]:
    return container.tools.policy()


@app.post("/api/dashboard/user-settings")
async def update_policy(payload: PolicyUpdate) -> dict[str, Any]:
    policy = container.tools.update_policy(
        web_search_enabled=payload.web_search_enabled,
        auto_update_enabled=payload.auto_update_enabled,
    )
    await container.sources.publish({"type": "policy_updated", "policy": policy})
    return policy


@app.get("/api/streams")
def streams() -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in container.sources.health()]


@app.get("/api/streams/{stream_id}/health")
def stream_health(stream_id: str) -> dict[str, Any]:
    item = next((item for item in container.sources.health() if item.stream_id == stream_id), None)
    if item is None:
        raise HTTPException(404, detail="Unknown stream")
    return item.model_dump(mode="json")


@app.get("/api/dashboard/state")
def dashboard_state() -> dict[str, Any]:
    spec = container.dashboard.current()
    if spec is None:
        raise HTTPException(503, detail="Dashboard not initialized")
    return spec.model_dump(mode="json")


@app.get("/api/dashboard/configs")
def dashboard_configs() -> list[dict]:
    return container.dashboard.history()


@app.get("/api/dashboard/configs/{version}")
def dashboard_config(version: int) -> dict[str, Any]:
    for item in container.dashboard.history():
        if item["version"] == version:
            return item
    raise HTTPException(404, detail="Unknown dashboard version")


@app.post("/api/dashboard/configs/{version}/restore")
async def restore_dashboard(version: int) -> dict[str, Any]:
    try:
        spec = container.dashboard.restore(version)
    except KeyError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    await container.sources.publish(
        {"type": "dashboard_updated", "dashboard_version": spec.version}
    )
    return spec.model_dump(mode="json")


@app.post("/api/dashboard/render-ack")
def render_ack(payload: RenderAck) -> dict[str, bool]:
    container.dashboard.acknowledge_render(
        payload.dashboard_version,
        payload.source_bundle_version,
        payload.handle_ids,
        payload.status,
        payload.error,
    )
    return {"ok": True}


@app.get("/api/dashboard/events")
async def dashboard_events(request: Request):
    async def stream():
        yield f"data: {json.dumps({'type': 'connected'})}\n\n"
        async for event in container.sources.subscribe():
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(event, ensure_ascii=True)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/handles/{handle_id}/preview")
def handle_preview(handle_id: str) -> dict[str, Any]:
    try:
        handle, rows = container.query.load(handle_id)
    except KeyError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    return {"handle": handle.model_dump(mode="json"), "rows": rows[:20]}


@app.get("/api/assets/{asset_handle}")
def asset(asset_handle: str):
    resolved = container.assets.resolve(asset_handle)
    if resolved is None:
        raise HTTPException(404, detail="Unknown asset")
    path, row = resolved
    return FileResponse(path, media_type=row.media_type, filename=f"{asset_handle}.{row.suffix}")


@app.post("/api/tools/{tool_name}")
async def execute_tool(tool_name: str, request: ToolRequest):
    normalized = tool_name.replace("-", "_")
    response = await container.tools.execute(normalized, request)
    if response.ok:
        if normalized == "dashboard_batch":
            await container.sources.publish(
                {"type": "dashboard_updated", "dashboard_version": response.dashboard_version}
            )
        return response.model_dump(mode="json")
    return JSONResponse(status_code=400, content=response.model_dump(mode="json"))


@app.post("/api/metrics/event")
def metric_event(payload: MetricEvent) -> dict[str, bool]:
    with container.database.session() as session:
        session.add(
            LatencyEventRow(
                conversation_id=payload.conversation_id,
                turn_id=payload.turn_id,
                event_type=payload.event_type,
                monotonic_ns=int(payload.monotonic_ms * 1_000_000),
                wall_clock=datetime.now(UTC).isoformat(),
                payload_json=json.dumps(payload.payload, ensure_ascii=True),
            )
        )
    return {"ok": True}


@app.get("/api/audit/tools")
def tool_audit(limit: int = 100) -> list[dict[str, Any]]:
    with container.database.session() as session:
        rows = session.scalars(
            select(ToolAuditRow).order_by(ToolAuditRow.started_ns.desc()).limit(min(limit, 500))
        ).all()
        return [
            {
                "audit_id": row.audit_id,
                "conversation_id": row.conversation_id,
                "tool_name": row.tool_name,
                "arguments": json.loads(row.arguments_json),
                "result": json.loads(row.result_json) if row.result_json else None,
                "ok": row.ok,
                "error_code": row.error_code,
                "duration_ms": row.duration_ms,
                "dashboard_before": row.dashboard_before,
                "dashboard_after": row.dashboard_after,
                "source_bundle_version": row.source_bundle_version,
            }
            for row in rows
        ]


@app.get("/api/audit/provider-calls")
def provider_calls(limit: int = 100) -> list[dict[str, Any]]:
    with container.database.session() as session:
        rows = session.scalars(
            select(ProviderCallRow)
            .order_by(ProviderCallRow.created_at.desc())
            .limit(min(max(limit, 1), 500))
        ).all()
        return [
            {
                "call_id": row.call_id,
                "provider": row.provider,
                "operation": row.operation,
                "status": row.status,
                "duration_ms": row.duration_ms,
                "usage": json.loads(row.usage_json),
                "created_at": row.created_at,
            }
            for row in rows
        ]


def evaluation_turns() -> list[dict[str, Any]]:
    with container.database.session() as session:
        rows = session.scalars(
            select(LatencyEventRow).order_by(LatencyEventRow.id.asc()).limit(10000)
        ).all()
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.turn_id or f"session:{row.conversation_id or 'local'}"
        turn = grouped.setdefault(
            key,
            {
                "turn_id": row.turn_id,
                "conversation_id": row.conversation_id,
                "events": [],
                "latencies_ms": {},
            },
        )
        turn["events"].append(
            {
                "type": row.event_type,
                "monotonic_ms": row.monotonic_ns / 1_000_000,
                "wall_clock": row.wall_clock,
                "payload": json.loads(row.payload_json),
            }
        )
    for turn in grouped.values():
        first: dict[str, float] = {}
        for event in turn["events"]:
            first.setdefault(event["type"], event["monotonic_ms"])
        origin = first.get("end_of_user_speech") or first.get("user_transcript")
        if origin is not None:
            for label, event_type in {
                "final_transcript": "final_transcript",
                "first_agent_text": "agent_text",
                "first_tool_call": "tool_execution_start",
                "dashboard_commit": "dashboard_commit_accepted",
                "render_complete": "render_complete",
                "first_playable_audio": "first_playable_audio",
                "turn_complete": "turn_complete",
            }.items():
                if event_type in first:
                    turn["latencies_ms"][label] = round(first[event_type] - origin, 2)
    return list(grouped.values())[-500:]


@app.get("/api/evaluation/turns")
def evaluation_turns_endpoint() -> list[dict[str, Any]]:
    return evaluation_turns()


@app.get("/api/evaluation/cases")
def evaluation_cases() -> list[dict[str, Any]]:
    path = settings.root / "data/evaluation/cases.json"
    return json.loads(path.read_text()) if path.exists() else []


def _csv_response(rows: list[dict[str, Any]], filename: str) -> StreamingResponse:
    output = io.StringIO()
    fields = sorted({key for row in rows for key in row})
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/export/metrics.csv")
def export_metrics_csv():
    rows = []
    for turn in evaluation_turns():
        rows.append(
            {
                "turn_id": turn["turn_id"],
                "conversation_id": turn["conversation_id"],
                "event_count": len(turn["events"]),
                **turn["latencies_ms"],
            }
        )
    return _csv_response(rows, "talk2dashboard-metrics.csv")


@app.get("/api/export/tools.csv")
def export_tools_csv():
    return _csv_response(
        [
            {
                **row,
                "arguments": json.dumps(row["arguments"], ensure_ascii=True),
                "result": json.dumps(row["result"], ensure_ascii=True),
            }
            for row in tool_audit(500)
        ],
        "talk2dashboard-tools.csv",
    )


@app.get("/api/export/state.json")
def export_state() -> dict[str, Any]:
    current = container.dashboard.current()
    assert current is not None
    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "dashboard": current.model_dump(mode="json"),
        "history": container.dashboard.history(),
        "sources": [item.model_dump(mode="json") for item in container.sources.health()],
        "policy": container.tools.policy(),
        "tools": tool_audit(500),
        "conversation_events": conversation_events(2000),
        "provider_calls": provider_calls(500),
    }


@app.get("/tokens.css", include_in_schema=False)
def tokens_css():
    return FileResponse(settings.root / "tokens.css", media_type="text/css")


renderer = create_renderer(
    RendererContext(container.dashboard, container.query, container.sources),
    Path(__file__).resolve().parents[1] / "renderer/assets",
)
app.mount("/", cast(Any, WSGIMiddleware(renderer.dash.server)))
