from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import talk2dashboard.config as config  # noqa: E402
from talk2dashboard.config import ROOT, Settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Start Talk2Dashboard without .env or live APIs.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    runtime = Path(tempfile.mkdtemp(prefix="talk2dashboard-fixture-"))
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        app_host=args.host,
        app_port=args.port,
        database_path=runtime / "fixture.sqlite3",
        asset_dir=runtime / "assets",
        raw_dir=runtime / "raw",
        fixture_dir=ROOT / "data/fixtures",
        fixture_only=True,
        p2000_provider_mode="disabled",
    )
    config.get_settings = lambda: settings  # type: ignore[assignment]
    from talk2dashboard.api.app import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
