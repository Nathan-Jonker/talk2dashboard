from __future__ import annotations

import hashlib
import math
from collections import deque
from datetime import timedelta
from typing import Literal, cast

from talk2dashboard.domain import EventRecord, IncidentCluster


def _distance_m(a: EventRecord, b: EventRecord) -> float:
    if not a.location or not b.location:
        return math.inf
    radius = 6_371_000.0
    lat1, lat2 = math.radians(a.location.latitude), math.radians(b.location.latitude)
    d_lat = lat2 - lat1
    d_lon = math.radians(b.location.longitude - a.location.longitude)
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def cluster_events(
    events: list[EventRecord],
    *,
    distance_m: int,
    window_minutes: int,
    active_min_events: int,
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
                if _distance_m(ordered[current], ordered[candidate]) > distance_m:
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
        confidence = min(1.0, 0.35 + 0.2 * len(members) + 0.1 * max(0, len(streams) - 1))
        clusters.append(
            IncidentCluster(
                incident_id=f"inc_{digest}",
                title=members[0].title,
                status="active" if len(members) >= active_min_events else "candidate",
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
