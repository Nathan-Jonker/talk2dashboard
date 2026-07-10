.PHONY: install dev fixture test lint typecheck frontend frontend-dev capture-install agent-check agent-sync smoke quality

install:
	UV_CACHE_DIR=.uv-cache uv sync --all-extras
	cd voice_dock && npm install
	cd voice_dock && npm run build

dev:
	UV_CACHE_DIR=.uv-cache uv run talk2dashboard

fixture:
	UV_CACHE_DIR=.uv-cache uv run python scripts/run_fixture_app.py --port 8001

test:
	UV_CACHE_DIR=.uv-cache uv run pytest
	cd voice_dock && npm test -- --run

lint:
	UV_CACHE_DIR=.uv-cache uv run ruff check src tests scripts
	cd voice_dock && npm run lint

typecheck:
	UV_CACHE_DIR=.uv-cache uv run pyright
	cd voice_dock && npm run typecheck

frontend:
	cd voice_dock && npm run build

frontend-dev:
	cd voice_dock && npm run dev

capture-install:
	UV_CACHE_DIR=.uv-cache uv run playwright install chromium

agent-check:
	UV_CACHE_DIR=.uv-cache uv run python scripts/sync_elevenlabs_agent.py --check

agent-sync:
	UV_CACHE_DIR=.uv-cache uv run python scripts/sync_elevenlabs_agent.py --apply

smoke:
	UV_CACHE_DIR=.uv-cache uv run python scripts/smoke_live_sources.py

quality: lint typecheck test frontend
