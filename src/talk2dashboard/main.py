from __future__ import annotations

import logging

import uvicorn

from talk2dashboard.config import get_settings


def run() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.app_log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(
        "talk2dashboard.api.app:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
        log_level=settings.app_log_level.lower(),
    )


if __name__ == "__main__":
    run()
