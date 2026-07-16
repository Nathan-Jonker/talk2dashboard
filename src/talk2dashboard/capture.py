from __future__ import annotations

from typing import Any

from talk2dashboard.config import Settings
from talk2dashboard.dashboard import DashboardService
from talk2dashboard.integrations.cerebras import CerebrasService
from talk2dashboard.storage.assets import AssetStore


class CaptureService:
    def __init__(
        self,
        settings: Settings,
        dashboard: DashboardService,
        assets: AssetStore,
        cerebras: CerebrasService | None = None,
    ) -> None:
        self.settings = settings
        self.dashboard = dashboard
        self.assets = assets
        self.cerebras = cerebras

    async def capture(self, payload: dict[str, Any]) -> dict[str, Any]:
        requested_version = int(payload["dashboard_version"])
        requested = self.dashboard.get_version(requested_version)
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("PLAYWRIGHT_NOT_INSTALLED") from exc
        scope = payload.get("scope", "full_dashboard")
        wait_for_render_ms = max(500, min(int(payload.get("wait_for_render_ms", 2000)), 10000))
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(headless=True)
            except Exception as exc:
                raise RuntimeError("PLAYWRIGHT_CHROMIUM_NOT_INSTALLED") from exc
            page = await browser.new_page(
                viewport={"width": 1440, "height": 1000}, device_scale_factor=1
            )
            await page.goto(
                f"http://{self.settings.app_host}:{self.settings.app_port}/"
                f"?capture=1&dashboard_version={requested_version}",
                wait_until="domcontentloaded",
            )
            try:
                await page.wait_for_function(
                    f"window.__talk2dRenderReady && "
                    f"window.__talk2dRenderReady.dashboard_version === {requested_version}",
                    timeout=wait_for_render_ms,
                )
            except Exception:
                await browser.close()
                return {
                    "status": "warning",
                    "warning": {
                        "code": "CAPTURE_RENDER_TIMEOUT",
                        "message": "Dashboard werd niet volledig bevestigd binnen de wachttijd.",
                        "retryable": True,
                    },
                    "dashboard_version": requested_version,
                    "structured_state": requested.model_dump(mode="json"),
                    "screenshot_handle": None,
                    "asset_url": None,
                }
            if scope == "panel":
                panel_id = payload.get("panel_id")
                if not panel_id:
                    raise ValueError("panel_id is required for panel capture")
                screenshot = await page.locator(f'[id*="{panel_id}"]').first.screenshot(type="png")
            elif scope == "viewport":
                screenshot = await page.screenshot(type="png", full_page=False)
            else:
                screenshot = await page.screenshot(type="png", full_page=True)
            await browser.close()
        handle = self.assets.put(
            screenshot,
            media_type="image/png",
            suffix="png",
            metadata={"dashboard_version": requested_version, "scope": scope},
            ttl_seconds=86400,
        )
        self.dashboard.link_screenshot(requested_version, handle)
        result = {
            "screenshot_handle": handle,
            "asset_url": f"/api/assets/{handle}",
            "dashboard_version": requested_version,
            "structured_state": requested.model_dump(mode="json")
            if payload.get("include_structured_state", True)
            else None,
        }
        if payload.get("analyze", False):
            if self.cerebras is None or not self.cerebras.configured:
                result["analysis"] = {"status": "unavailable", "reason": "CEREBRAS_NOT_CONFIGURED"}
            else:
                result["analysis"] = await self.cerebras.analyze_dashboard(
                    screenshot, requested.model_dump(mode="json")
                )
        return result
