from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class SourceSnapshotRow(Base):
    __tablename__ = "source_snapshots"
    snapshot_id: Mapped[str] = mapped_column(String, primary_key=True)
    stream_id: Mapped[str] = mapped_column(String, index=True)
    provider: Mapped[str] = mapped_column(String)
    content_hash: Mapped[str] = mapped_column(String)
    source_url: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[str] = mapped_column(String)
    ingested_at: Mapped[str] = mapped_column(String, default=lambda: utcnow().isoformat())
    status: Mapped[str] = mapped_column(String, default="success")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class NormalizedRecordRow(Base):
    __tablename__ = "normalized_record_versions"
    __table_args__ = (
        Index(
            "ix_normalized_stream_record_observed",
            "stream_id",
            "record_id",
            "observed_at",
        ),
    )
    record_version_id: Mapped[str] = mapped_column(String, primary_key=True)
    record_id: Mapped[str] = mapped_column(String, index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("source_snapshots.snapshot_id"), index=True)
    stream_id: Mapped[str] = mapped_column(String, index=True)
    record_kind: Mapped[str] = mapped_column(String, index=True)
    observed_at: Mapped[str] = mapped_column(String, index=True)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    payload_json: Mapped[str] = mapped_column(Text)


class IncidentClusterRow(Base):
    __tablename__ = "incident_clusters"
    incident_version_id: Mapped[str] = mapped_column(String, primary_key=True)
    incident_id: Mapped[str] = mapped_column(String, index=True)
    bundle_version: Mapped[str] = mapped_column(String, index=True)
    first_seen_at: Mapped[str] = mapped_column(String)
    last_seen_at: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, index=True)
    confidence: Mapped[float] = mapped_column(Float)
    payload_json: Mapped[str] = mapped_column(Text)


class SourceBundleRow(Base):
    __tablename__ = "source_bundles"
    bundle_version: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[str] = mapped_column(String, default=lambda: utcnow().isoformat())
    snapshot_ids_json: Mapped[str] = mapped_column(Text)
    health_json: Mapped[str] = mapped_column(Text)


class DataHandleRow(Base):
    __tablename__ = "data_handles"
    handle_id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, index=True)
    source_bundle_version: Mapped[str] = mapped_column(String, index=True)
    query_hash: Mapped[str] = mapped_column(String, index=True)
    schema_fingerprint: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
    expires_at: Mapped[str | None] = mapped_column(String)
    payload_json: Mapped[str] = mapped_column(Text)


class DashboardConfigRow(Base):
    __tablename__ = "dashboard_configs"
    config_version_id: Mapped[str] = mapped_column(String, primary_key=True)
    dashboard_id: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[int] = mapped_column(Integer, index=True)
    previous_version: Mapped[int | None] = mapped_column(Integer)
    spec_json: Mapped[str] = mapped_column(Text)
    json_patch: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[str] = mapped_column(String)
    created_by: Mapped[str] = mapped_column(String)
    conversation_id: Mapped[str | None] = mapped_column(String)
    turn_id: Mapped[str | None] = mapped_column(String)
    reason: Mapped[str] = mapped_column(Text)
    source_bundle_version: Mapped[str] = mapped_column(String)
    logical_binding_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    last_rendered_source_bundle_version: Mapped[str | None] = mapped_column(String)
    last_rendered_handle_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    renderer_status: Mapped[str] = mapped_column(String, default="pending")
    screenshot_handle: Mapped[str | None] = mapped_column(String)
    restored_from_version: Mapped[int | None] = mapped_column(Integer)


class RenderStateRow(Base):
    __tablename__ = "render_states"
    dashboard_id: Mapped[str] = mapped_column(String, primary_key=True)
    dashboard_version: Mapped[int] = mapped_column(Integer)
    source_bundle_version: Mapped[str] = mapped_column(String)
    handle_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String, default="pending")
    updated_at: Mapped[str] = mapped_column(String)
    error: Mapped[str | None] = mapped_column(Text)


class SessionPolicyRow(Base):
    __tablename__ = "session_policy"
    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(Integer, default=1)
    web_search_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_update_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[str] = mapped_column(String)


class ConversationRow(Base):
    __tablename__ = "conversations"
    conversation_id: Mapped[str] = mapped_column(String, primary_key=True)
    started_at: Mapped[str] = mapped_column(String)
    ended_at: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    voice_id: Mapped[str] = mapped_column(String)


class ConversationEventRow(Base):
    __tablename__ = "conversation_events"
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True)
    turn_id: Mapped[str | None] = mapped_column(String, index=True)
    role: Mapped[str] = mapped_column(String, index=True)
    text: Mapped[str] = mapped_column(Text)
    final: Mapped[bool] = mapped_column(Boolean, default=True)
    model: Mapped[str] = mapped_column(String)
    prompt_version: Mapped[str] = mapped_column(String)
    dashboard_version: Mapped[int | None] = mapped_column(Integer)
    source_bundle_version: Mapped[str | None] = mapped_column(String)
    handle_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[str] = mapped_column(String, index=True)


class ToolAuditRow(Base):
    __tablename__ = "tool_audit"
    audit_id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(String, index=True)
    turn_id: Mapped[str | None] = mapped_column(String, index=True)
    request_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    tool_name: Mapped[str] = mapped_column(String, index=True)
    arguments_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text)
    ok: Mapped[bool] = mapped_column(Boolean)
    error_code: Mapped[str | None] = mapped_column(String)
    error_json: Mapped[str | None] = mapped_column(Text)
    started_ns: Mapped[int] = mapped_column(Integer)
    ended_ns: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[float] = mapped_column(Float)
    dashboard_before: Mapped[int | None] = mapped_column(Integer)
    dashboard_after: Mapped[int | None] = mapped_column(Integer)
    source_bundle_version: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, index=True)


class LatencyEventRow(Base):
    __tablename__ = "latency_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str | None] = mapped_column(String, index=True)
    turn_id: Mapped[str | None] = mapped_column(String, index=True)
    event_type: Mapped[str] = mapped_column(String, index=True)
    monotonic_ns: Mapped[int] = mapped_column(Integer)
    wall_clock: Mapped[str] = mapped_column(String)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")


class AssetRow(Base):
    __tablename__ = "assets"
    asset_handle: Mapped[str] = mapped_column(String, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String, unique=True)
    media_type: Mapped[str] = mapped_column(String)
    suffix: Mapped[str] = mapped_column(String)
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String)
    expires_at: Mapped[str | None] = mapped_column(String)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class BudgetCounterRow(Base):
    __tablename__ = "budget_counters"
    counter_key: Mapped[str] = mapped_column(String, primary_key=True)
    day: Mapped[str] = mapped_column(String, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[str] = mapped_column(String)


class ProviderCallRow(Base):
    __tablename__ = "provider_calls"
    call_id: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str] = mapped_column(String, index=True)
    operation: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String)
    duration_ms: Mapped[float] = mapped_column(Float)
    usage_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String, index=True)


class EphemeralLocationResolutionRow(Base):
    __tablename__ = "ephemeral_location_resolutions"
    resolution_id: Mapped[str] = mapped_column(String, primary_key=True)
    input_hash: Mapped[str] = mapped_column(String, index=True)
    google_place_id: Mapped[str | None] = mapped_column(String)
    display_label: Mapped[str] = mapped_column(Text)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    attribution: Mapped[str] = mapped_column(String)
    policy_version: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
    expires_at: Mapped[str] = mapped_column(String, index=True)


class ClaimAuditRow(Base):
    __tablename__ = "claim_audits"
    claim_id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True)
    turn_id: Mapped[str | None] = mapped_column(String, index=True)
    event_id: Mapped[str] = mapped_column(String, index=True)
    claim_text: Mapped[str] = mapped_column(Text)
    numeric_value: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, index=True)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[str] = mapped_column(String, index=True)
