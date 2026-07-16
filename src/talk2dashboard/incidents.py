from __future__ import annotations

import hashlib
from collections import deque
from datetime import datetime, timedelta
from typing import Literal, cast

from talk2dashboard.deterministic import haversine_m
from talk2dashboard.domain import EventRecord, IncidentCluster


def _distance_m(a: EventRecord, b: EventRecord) -> float:
    if not a.location or not b.location:
        return float("inf")
    return haversine_m(
        a.location.latitude,
        a.location.longitude,
        b.location.latitude,
        b.location.longitude,
    )


def cluster_events(
    events: list[EventRecord],
    *,
    distance_m: int,
    window_minutes: int,
    active_min_events: int,
    resolve_after_minutes: int = 120,
    as_of: datetime | None = None,
) -> list[IncidentCluster]:
    """Deterministic connected-components clustering over time and source geometry."""
    ordered = sorted(events, key=lambda item: (item.observed_at, item.record_id))
    unseen = set(range(len(ordered)))
    clusters: list[IncidentCluster] = []
    window = timedelta(minutes=window_minutes)
    while unseen:
        seed = min(unseen)
        unseen.remove(seed)
        component = [seed]
        queue = deque([seed])
        while queue:
            current = queue.popleft()
            for candidate in sorted(unseen):
                if abs(ordered[candidate].observed_at - ordered[current].observed_at) > window:
                    continue
                current_location = ordered[current].location
                candidate_location = ordered[candidate].location
                current_radius = current_location.uncertainty_radius_m if current_location else 0
                candidate_radius = (
                    candidate_location.uncertainty_radius_m if candidate_location else 0
                )
                if (
                    _distance_m(ordered[current], ordered[candidate])
                    > distance_m + current_radius + candidate_radius
                ):
                    continue
                unseen.remove(candidate)
                component.append(candidate)
                queue.append(candidate)
        members = [ordered[index] for index in sorted(component)]
        event_ids = tuple(item.record_id for item in members)
        digest = hashlib.sha256("|".join(event_ids).encode()).hexdigest()[:20]
        severities = ["unknown", "low", "medium", "high", "critical"]
        severity = cast(
            Literal["low", "medium", "high", "critical", "unknown"],
            max((item.severity for item in members), key=severities.index),
        )
        streams = tuple(sorted({item.stream_id for item in members}))
        spatial_spread = max(
            (_distance_m(left, right) for left in members for right in members), default=0.0
        )
        time_spread = (
            max(item.observed_at for item in members) - min(item.observed_at for item in members)
        ).total_seconds()
        severity_score = {
            "unknown": 0.0,
            "low": 0.03,
            "medium": 0.07,
            "high": 0.13,
            "critical": 0.18,
        }[severity]
        confidence = min(
            1.0,
            0.22
            + min(len(members), 4) * 0.13
            + min(len(streams) - 1, 3) * 0.1
            + severity_score
            + (0.08 if time_spread <= window.total_seconds() / 2 else 0.03)
            + (0.08 if spatial_spread <= distance_m / 2 else 0.03),
        )
        last_seen = max(item.observed_at for item in members)
        reference_time = as_of or last_seen
        promoted = len(members) >= active_min_events or severity in {"high", "critical"}
        status: Literal["candidate", "active", "resolved"] = (
            "resolved"
            if reference_time - last_seen >= timedelta(minutes=resolve_after_minutes)
            else "active"
            if promoted
            else "candidate"
        )
        clusters.append(
            IncidentCluster(
                incident_id=f"inc_{digest}",
                title=members[0].title,
                status=status,
                confidence=confidence,
                severity=severity,
                first_seen_at=min(item.observed_at for item in members),
                last_seen_at=max(item.observed_at for item in members),
                event_ids=event_ids,
                stream_ids=streams,
                location=next((item.location for item in members if item.location), None),
            )
        )
    return sorted(clusters, key=lambda item: (item.last_seen_at, item.incident_id), reverse=True)
