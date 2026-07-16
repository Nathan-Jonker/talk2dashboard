from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MULTI_BINDING_PANEL_TYPES = frozenset(
    {
        "kpi",
        "timeseries",
        "ranking",
        "comparison",
        "incident_timeline",
        "event_table",
        "evidence",
        "map_2d",
        "map_3d_google",
        "nearby_places",
        "change_summary",
        "ai_brief",
    }
)
MAX_VISIBLE_PANELS = 12


class TrustTier(StrEnum):
    OFFICIAL_MEASUREMENT = "official_measurement"
    OFFICIAL_WARNING = "official_warning"
    OFFICIAL_OPERATIONAL = "official_operational_feed"
    OFFICIAL_REFERENCE = "official_reference"
    MEDIA_CONTEXT = "media_context"
    SIGNAL = "signal"
    COMMERCIAL_REFERENCE = "commercial_reference"
    UNVERIFIED_EXTERNAL = "unverified_external"
    FIXTURE = "fixture"


class SourceHealthStatus(StrEnum):
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    DISABLED = "disabled"
    STALE = "stale"
    FIXTURE = "fixture"


class SourceRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    stream_id: str
    record_id: str
    source_url: str | None = None
    owner: str
    trust_tier: TrustTier
    observed_at: datetime
    ingested_at: datetime


class LocationRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    location_id: str
    label: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    uncertainty_radius_m: float = Field(default=0, ge=0)
    geometry_source: Literal["source", "pdok", "open_reference", "fixture", "manual"]
    source_refs: tuple[SourceRef, ...] = ()


class EphemeralLocationResolution(BaseModel):
    model_config = ConfigDict(frozen=True)

    resolution_id: str
    input_hash: str
    google_place_id: str | None = None
    display_label: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    requested_at: datetime
    expires_at: datetime
    attribution: str = "Google Maps"
    terms_profile: str = "google-geocoding-transient-v1"


class EventRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    record_id: str
    stream_id: str
    category: str
    title: str
    description: str | None = None
    severity: Literal["low", "medium", "high", "critical", "unknown"] = "unknown"
    status: str = "active"
    observed_at: datetime
    ingested_at: datetime
    location: LocationRef | None = None
    attributes: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    source_ref: SourceRef
    quality_flags: tuple[str, ...] = ()


class MeasurementRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    record_id: str
    stream_id: str
    metric: str
    value: float
    unit: str
    observed_at: datetime
    ingested_at: datetime
    location: LocationRef | None = None
    source_ref: SourceRef
    quality_flags: tuple[str, ...] = ()


class IncidentCluster(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident_id: str
    title: str
    status: Literal["candidate", "active", "resolved"]
    confidence: float = Field(ge=0, le=1)
    severity: Literal["low", "medium", "high", "critical", "unknown"]
    first_seen_at: datetime
    last_seen_at: datetime
    event_ids: tuple[str, ...]
    stream_ids: tuple[str, ...]
    location: LocationRef | None = None


class SourceHealth(BaseModel):
    stream_id: str
    owner: str
    status: SourceHealthStatus
    last_success_at: datetime | None = None
    newest_record_at: datetime | None = None
    expected_cadence_seconds: int
    record_count: int = 0
    error_code: str | None = None
    message: str | None = None
    provider: str | None = None
    age_seconds: float | None = None
    fixture: bool = False
    fallback: bool = False


type HandleKind = Literal[
    "events",
    "series",
    "aggregate",
    "baseline",
    "correlation",
    "incident",
    "places",
    "web_results",
    "diff",
    "location",
]


class DataHandle(BaseModel):
    model_config = ConfigDict(frozen=True)

    handle_id: str
    kind: HandleKind
    created_at: datetime
    expires_at: datetime | None = None
    source_bundle_version: str
    schema_fingerprint: str
    fields: tuple[str, ...]
    row_count: int
    preview: tuple[dict[str, Any], ...]
    summary: str
    source_refs: tuple[str, ...]
    query_hash: str
    freshness: dict[str, datetime | None]


class LogicalDataBinding(BaseModel):
    binding_id: str
    kind: HandleKind
    query_hash: str
    query_spec: dict[str, Any]
    field_bindings: dict[str, str]
    refresh_policy: Literal["visible", "manual", "paused"] = "visible"
    schema_fingerprint: str


class PanelSpec(BaseModel):
    panel_id: str
    panel_type: Literal[
        "kpi",
        "timeseries",
        "ranking",
        "comparison",
        "incident_timeline",
        "event_table",
        "source_health",
        "evidence",
        "map_2d",
        "map_3d_google",
        "nearby_places",
        "correlation",
        "change_summary",
        "ai_brief",
    ]
    title: str = Field(min_length=1, max_length=100)
    binding: LogicalDataBinding | None = None
    layer_bindings: list[LogicalDataBinding] = Field(default_factory=list, max_length=5)
    span: Literal["compact", "standard", "wide", "full"] = "standard"
    order: int = Field(default=0, ge=0, le=100)
    props: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_layer_bindings(self) -> PanelSpec:
        if self.layer_bindings and self.panel_type not in MULTI_BINDING_PANEL_TYPES:
            raise ValueError(f"layer_bindings are not supported by {self.panel_type}")
        bindings = ([self.binding] if self.binding else []) + self.layer_bindings
        binding_ids = [binding.binding_id for binding in bindings]
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("panel binding_id must be unique")
        return self

    @property
    def bindings(self) -> tuple[LogicalDataBinding, ...]:
        return tuple(binding for binding in (self.binding, *self.layer_bindings) if binding)


class DashboardSpec(BaseModel):
    dashboard_id: str = "main"
    version: int = Field(default=1, ge=1)
    created_from_source_bundle_version: str
    title: str = "Operationeel beeld"
    subtitle: str | None = None
    layout_template: Literal[
        "incident_focus",
        "trend_focus",
        "mobility_focus",
        "environment_focus",
        "evidence_focus",
        "briefing",
    ] = "incident_focus"
    refresh_focus: Literal[
        "manual", "visible_handles", "incident_follow", "map_extent", "paused"
    ] = "visible_handles"
    global_filters: list[dict[str, Any]] = Field(default_factory=list)
    map_focus: str | None = None
    panels: list[PanelSpec] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: Literal["system", "user", "agent"] = "system"
    conversation_id: str | None = None
    reason: str = "initial dashboard"

    @field_validator("panels")
    @classmethod
    def unique_panel_ids(cls, panels: list[PanelSpec]) -> list[PanelSpec]:
        ids = [panel.panel_id for panel in panels]
        if len(ids) != len(set(ids)):
            raise ValueError("panel_id must be unique")
        visible_count = sum(
            panel.panel_type not in {"source_health", "evidence"} for panel in panels
        )
        if visible_count > MAX_VISIBLE_PANELS:
            raise ValueError(f"dashboard supports at most {MAX_VISIBLE_PANELS} visible panels")
        return sorted(panels, key=lambda item: item.order)


class DashboardOperation(BaseModel):
    op: Literal[
        "set_meta",
        "set_layout_template",
        "upsert_panel",
        "remove_panel",
        "set_global_filter",
        "set_focus",
        "set_map_mode",
        "undo",
    ]
    panel: PanelSpec | None = None
    panel_id: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_operation(self) -> DashboardOperation:
        if self.op == "upsert_panel" and self.panel is None:
            raise ValueError("upsert_panel requires panel")
        if self.op == "remove_panel" and not self.panel_id:
            raise ValueError("remove_panel requires panel_id")
        return self


class ToolRequest(BaseModel):
    conversation_id: str | None = None
    turn_id: str | None = None
    request_id: str
    session_policy_version: int = 1
    dashboard_version: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolResponse(BaseModel):
    ok: bool
    result: dict[str, Any] | list[Any] | None = None
    error: dict[str, Any] | None = None
    duration_ms: float
    source_bundle_version: str | None = None
    dashboard_version: int | None = None
    audit_ref: str
