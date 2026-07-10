from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from talk2dashboard.domain import EventRecord, MeasurementRecord, SourceHealth, SourceHealthStatus


@dataclass(slots=True)
class AdapterResult:
    stream_id: str
    provider: str
    source_url: str | None
    raw: bytes
    observed_at: datetime
    events: list[EventRecord] = field(default_factory=list)
    measurements: list[MeasurementRecord] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class SourceAdapter(ABC):
    stream_id: str
    owner: str
    expected_cadence_seconds: int
    provider: str

    def __init__(self) -> None:
        self.health = SourceHealth(
            stream_id=self.stream_id,
            owner=self.owner,
            status=SourceHealthStatus.STARTING,
            expected_cadence_seconds=self.expected_cadence_seconds,
            provider=self.provider,
        )

    @abstractmethod
    async def fetch(self) -> AdapterResult:
        raise NotImplementedError

    def mark_success(self, result: AdapterResult) -> None:
        now = datetime.now(UTC)
        observed = [record.observed_at for record in [*result.events, *result.measurements]]
        self.health = self.health.model_copy(
            update={
                "status": SourceHealthStatus.HEALTHY,
                "last_success_at": now,
                "newest_record_at": max(observed) if observed else result.observed_at,
                "record_count": len(result.events) + len(result.measurements),
                "error_code": None,
                "message": None,
                "provider": result.provider,
            }
        )

    def mark_failure(self, code: str, message: str) -> None:
        status = (
            SourceHealthStatus.DEGRADED
            if self.health.last_success_at
            else SourceHealthStatus.OFFLINE
        )
        self.health = self.health.model_copy(
            update={"status": status, "error_code": code, "message": message[:240]}
        )


class DisabledAdapter(SourceAdapter):
    def __init__(self, stream_id: str, owner: str, reason: str) -> None:
        self.stream_id = stream_id
        self.owner = owner
        self.provider = "disabled"
        self.expected_cadence_seconds = 3600
        super().__init__()
        self.health = self.health.model_copy(
            update={"status": SourceHealthStatus.DISABLED, "message": reason}
        )

    async def fetch(self) -> AdapterResult:
        raise RuntimeError(self.health.message or "source disabled")


class FallbackAdapter(SourceAdapter):
    """Expose one logical stream while failing over between explicit providers."""

    def __init__(self, primary: SourceAdapter, fallback: SourceAdapter) -> None:
        if primary.stream_id != fallback.stream_id:
            raise ValueError("Fallback adapters must expose the same stream")
        self.primary = primary
        self.fallback = fallback
        self.stream_id = primary.stream_id
        self.owner = primary.owner
        self.provider = f"{primary.provider} -> {fallback.provider}"
        self.expected_cadence_seconds = min(
            primary.expected_cadence_seconds, fallback.expected_cadence_seconds
        )
        super().__init__()

    async def fetch(self) -> AdapterResult:
        try:
            result = await self.primary.fetch()
            self.primary.mark_success(result)
            return result
        except Exception as exc:
            self.primary.mark_failure(type(exc).__name__, str(exc))
            result = await self.fallback.fetch()
            self.fallback.mark_success(result)
            result.metadata["fallback_from"] = self.primary.provider
            result.metadata["fallback_reason"] = type(exc).__name__
            return result
