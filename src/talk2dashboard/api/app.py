from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import websockets
from a2wsgi import WSGIMiddleware
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from websockets.typing import Subprotocol

from talk2dashboard.agent_catalog import STREAM_CAPABILITIES, STREAM_IDS, TOOL_CAPABILITIES
from talk2dashboard.capture import CaptureService
from talk2dashboard.claims import audit_numeric_claims
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
    ClaimAuditRow,
    ConversationEventRow,
    ConversationRow,
    LatencyEventRow,
    NormalizedRecordRow,
    ProviderCallRow,
    SourceBundleRow,
    SourceSnapshotRow,
    ToolAuditRow,
)
from talk2dashboard.tools.definitions import TOOL_DEFINITIONS
from talk2dashboard.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)


class PolicyUpdate(BaseModel):
    web_search_enabled: bool | None = None
    auto_update_enabled: bool | None = None


class DashboardInitializationRequest(BaseModel):
    force: bool = False


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


class FixtureSelection(BaseModel):
    fixture: str


class FixtureControl(BaseModel):
    stream_id: str
    mode: str


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
        self.planner = InitialDashboardPlanner(
            self.cerebras, self.sources, self.dashboard, self.tools
        )
        self.background_task: asyncio.Task | None = None
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


@app.get("/api/agent-tools")
def agent_tools() -> list[dict[str, Any]]:
    """Return the same bounded catalog that is provisioned to the voice agent."""
    result: list[dict[str, Any]] = []
    for definition in TOOL_DEFINITIONS:
        name = str(definition["name"])
        documentation = TOOL_CAPABILITIES[name]
        result.append(
            {
                "name": str(definition["name"]),
                "display_name": str(definition["display_name"]),
                "category": str(definition["category"]),
                "description": str(definition["description"]),
                "inputs": documentation["inputs"],
                "outputs": documentation["outputs"],
                "constraints": documentation["constraints"],
                "examples": documentation["examples"],
            }
        )
    return result


@app.get("/api/source-catalog")
def source_catalog() -> list[dict[str, Any]]:
    """Describe the stable read contracts without exposing provider credentials."""
    return [{"stream_id": stream_id, **STREAM_CAPABILITIES[stream_id]} for stream_id in STREAM_IDS]


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
    return {"signed_url": await _fetch_elevenlabs_signed_url()}


async def _fetch_elevenlabs_signed_url() -> str:
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
    return str(signed_url)


@app.websocket("/api/session/elevenlabs-proxy")
async def elevenlabs_proxy(browser: WebSocket) -> None:
    """Relay the ElevenLabs conversation transport through the local same-origin app."""
    host = browser.headers.get("host", "")
    origin = browser.headers.get("origin", "")
    if not host or origin not in {f"http://{host}", f"https://{host}"}:
        await browser.close(code=1008, reason="Same-origin connection required")
        return

    await browser.accept(subprotocol="convai")
    try:
        signed_url = await _fetch_elevenlabs_signed_url()
        client_query = browser.url.query
        upstream_url = f"{signed_url}&{client_query}" if client_query else signed_url
        async with websockets.connect(
            upstream_url,
            subprotocols=[Subprotocol("convai")],
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
        ) as upstream:

            async def browser_to_upstream() -> None:
                while True:
                    message = await browser.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("text") is not None:
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

            async def upstream_to_browser() -> None:
                async for message in upstream:
                    if isinstance(message, bytes):
                        await browser.send_bytes(message)
                    else:
                        await browser.send_text(message)

            tasks = {
                asyncio.create_task(browser_to_upstream()),
                asyncio.create_task(upstream_to_browser()),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
    except WebSocketDisconnect:
        return
    except Exception as error:
        logger.warning("ElevenLabs proxy disconnected: %s", type(error).__name__)
        with suppress(RuntimeError):
            await browser.close(code=1011, reason="ElevenLabs upstream unavailable")


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


@app.post("/api/session/stop-all")
async def stop_all() -> dict[str, Any]:
    ended = 0
    with container.database.session() as session:
        rows = session.scalars(
            select(ConversationRow).where(ConversationRow.status == "active")
        ).all()
        for row in rows:
            row.status = "ended"
            row.ended_at = datetime.now(UTC).isoformat()
            ended += 1
    policy = container.tools.update_policy(auto_update_enabled=False)
    await container.sources.publish({"type": "policy_updated", "policy": policy})
    return {"ok": True, "conversations_ended": ended, "auto_update_enabled": False}


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
    if payload.role == "agent" and payload.final:
        audit_numeric_claims(
            container.database,
            event_id=payload.event_id,
            conversation_id=payload.conversation_id,
            turn_id=payload.turn_id,
            text=payload.text,
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


@app.get("/api/evidence/{source_ref:path}")
def evidence(source_ref: str) -> dict[str, Any]:
    if ":" not in source_ref:
        raise HTTPException(400, detail={"code": "INVALID_SOURCE_REF"})
    stream_id, record_id = source_ref.split(":", 1)
    with container.database.session() as session:
        record = session.scalars(
            select(NormalizedRecordRow)
            .where(
                NormalizedRecordRow.stream_id == stream_id,
                NormalizedRecordRow.record_id == record_id,
            )
            .order_by(NormalizedRecordRow.observed_at.desc())
        ).first()
        if record is None:
            raise HTTPException(404, detail={"code": "EVIDENCE_NOT_FOUND"})
        snapshot = session.get(SourceSnapshotRow, record.snapshot_id)
        assert snapshot is not None
        bundles = session.scalars(select(SourceBundleRow)).all()
        bundle_ids = [
            row.bundle_version
            for row in bundles
            if record.snapshot_id in json.loads(row.snapshot_ids_json)
        ]
        normalized = json.loads(record.payload_json)
        metadata = json.loads(snapshot.metadata_json)
        ref = normalized.get("source_ref") or {}
        return {
            "source_ref": source_ref,
            "record": normalized,
            "snapshot": {
                "snapshot_id": snapshot.snapshot_id,
                "content_hash": snapshot.content_hash,
                "source_url": snapshot.source_url,
                "provider": snapshot.provider,
                "observed_at": snapshot.observed_at,
                "ingested_at": snapshot.ingested_at,
                "metadata": metadata,
            },
            "owner": ref.get("owner"),
            "trust_tier": ref.get("trust_tier"),
            "quality_flags": normalized.get("quality_flags", []),
            "bundle_versions": bundle_ids,
            "fallback": {
                "used": bool(metadata.get("fallback_from")),
                "from": metadata.get("fallback_from"),
                "reason": metadata.get("fallback_reason"),
            },
        }


@app.get("/api/dashboard/state")
def dashboard_state() -> dict[str, Any]:
    spec = container.dashboard.current()
    if spec is None:
        raise HTTPException(503, detail="Dashboard not initialized")
    return spec.model_dump(mode="json")


@app.post("/api/dashboard/initialize")
async def initialize_dashboard(payload: DashboardInitializationRequest) -> dict[str, Any]:
    """Compose the workspace when its persistent cooldown allows it, or on demand."""
    status_before = container.planner.cooldown_status()
    try:
        changed = await container.planner.initialize(force=payload.force)
    except Exception as exc:
        logger.warning("Initial dashboard planner failed: %s", type(exc).__name__)
        raise HTTPException(
            502,
            detail={
                "code": "INITIAL_DASHBOARD_PLANNER_FAILED",
                "message": "De actuele startcompositie kon niet worden opgebouwd.",
            },
        ) from exc
    spec = container.dashboard.current()
    assert spec is not None
    status_after = container.planner.cooldown_status()
    if changed:
        await container.sources.publish(
            {
                "type": "dashboard_updated",
                "dashboard_version": spec.version,
                "source_bundle_version": spec.created_from_source_bundle_version,
            }
        )
    return {
        "changed": changed,
        "forced": payload.force,
        "dashboard_version": spec.version,
        "decision": "redesigned" if changed else status_before["reason"],
        "last_dashboard_update_at": status_after["last_dashboard_update_at"],
        "next_automatic_at": status_after["next_automatic_at"],
        "cooldown_minutes": status_after["cooldown_minutes"],
    }


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
            select(ToolAuditRow).order_by(ToolAuditRow.created_at.desc()).limit(min(limit, 500))
        ).all()
        return [
            {
                "audit_id": row.audit_id,
                "conversation_id": row.conversation_id,
                "turn_id": row.turn_id,
                "tool_name": row.tool_name,
                "arguments": json.loads(row.arguments_json),
                "result": json.loads(row.result_json) if row.result_json else None,
                "ok": row.ok,
                "error_code": row.error_code,
                "error": json.loads(row.error_json) if row.error_json else None,
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


@app.post("/api/evaluation/fixtures/select")
async def select_fixture(payload: FixtureSelection) -> dict[str, Any]:
    if not settings.fixture_only:
        raise HTTPException(403, detail={"code": "FIXTURE_MODE_REQUIRED"})
    try:
        container.sources.select_fixture(payload.fixture)
        bundle = await container.sources.initialize_fixture()
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "FIXTURE_NOT_FOUND"}) from exc
    await container.sources.publish({"type": "source_bundle", "source_bundle_version": bundle})
    return {"ok": True, "fixture": payload.fixture, "source_bundle_version": bundle}


@app.post("/api/evaluation/fixtures/control")
def control_fixture(payload: FixtureControl) -> dict[str, Any]:
    if not settings.fixture_only:
        raise HTTPException(403, detail={"code": "FIXTURE_MODE_REQUIRED"})
    try:
        container.sources.control_fixture(payload.stream_id, payload.mode)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "FIXTURE_STREAM_NOT_FOUND"}) from exc
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "INVALID_FIXTURE_MODE"}) from exc
    return {"ok": True, "stream_id": payload.stream_id, "mode": payload.mode}


@app.get("/api/evaluation/claims")
def claim_audits(limit: int = 200) -> list[dict[str, Any]]:
    with container.database.session() as session:
        rows = session.scalars(
            select(ClaimAuditRow).order_by(ClaimAuditRow.created_at.desc()).limit(min(limit, 1000))
        ).all()
        return [
            {
                "claim_id": row.claim_id,
                "conversation_id": row.conversation_id,
                "turn_id": row.turn_id,
                "event_id": row.event_id,
                "claim_text": row.claim_text,
                "numeric_value": row.numeric_value,
                "status": row.status,
                "evidence": json.loads(row.evidence_json),
                "created_at": row.created_at,
            }
            for row in rows
        ]


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
