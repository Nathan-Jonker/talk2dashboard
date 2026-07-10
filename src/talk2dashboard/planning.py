from __future__ import annotations

from typing import ClassVar

from talk2dashboard.dashboard import DashboardService
from talk2dashboard.domain import DashboardOperation
from talk2dashboard.integrations.cerebras import CerebrasService
from talk2dashboard.sources.service import SourceService


class InitialDashboardPlanner:
    _layouts: ClassVar[set[str]] = {
        "incident_focus",
        "trend_focus",
        "mobility_focus",
        "environment_focus",
        "evidence_focus",
        "briefing",
    }

    def __init__(
        self,
        cerebras: CerebrasService,
        sources: SourceService,
        dashboard: DashboardService,
    ) -> None:
        self.cerebras = cerebras
        self.sources = sources
        self.dashboard = dashboard

    async def initialize(self) -> bool:
        if not self.cerebras.configured:
            return False
        current = self.dashboard.current(optional=True)
        if current is None or current.version != 1 or current.created_by != "system":
            return False
        source_summary = [
            {
                "stream_id": item.stream_id,
                "status": item.status,
                "record_count": item.record_count,
                "newest_record_at": item.newest_record_at.isoformat()
                if item.newest_record_at
                else None,
            }
            for item in self.sources.health()
        ]
        proposal = await self.cerebras.plan_focus(source_summary)
        layout = proposal.get("layout_template")
        if layout not in self._layouts:
            layout = "incident_focus"
        title = str(proposal.get("title") or current.title)[:60]
        subtitle = str(proposal.get("subtitle") or current.subtitle or "")[:120]
        self.dashboard.apply(
            current.version,
            [
                DashboardOperation(op="set_layout_template", values={"layout_template": layout}),
                DashboardOperation(op="set_meta", values={"title": title, "subtitle": subtitle}),
            ],
            created_by="agent",
            reason=f"initial Cerebras focus: {str(proposal.get('reason', 'live source focus'))[:120]}",
        )
        return True
