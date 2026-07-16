.PHONY: install dev fixture test lint typecheck frontend frontend-dev frontend-verify capture-install agent-check agent-sync agent-route-test agent-acceptance-test smoke quality

install:
	UV_CACHE_DIR=.uv-cache uv sync --all-extras
	cd voice_dock && npm install
	cd voice_dock && npm run build

dev:
	PYTHONPATH=src UV_CACHE_DIR=.uv-cache uv run python -m talk2dashboard.main

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

frontend-verify:
	@before=$$(git diff -- src/talk2dashboard/renderer/assets/voice-dock.js src/talk2dashboard/renderer/assets/voice-dock.css); \
	cd voice_dock && npm run build >/dev/null; \
	cd ..; after=$$(git diff -- src/talk2dashboard/renderer/assets/voice-dock.js src/talk2dashboard/renderer/assets/voice-dock.css); \
	test "$$before" = "$$after" || (echo "Generated voice assets differ; run make frontend and commit them." && exit 1)

frontend-dev:
	cd voice_dock && npm run dev

capture-install:
	UV_CACHE_DIR=.uv-cache uv run playwright install chromium

agent-check:
	UV_CACHE_DIR=.uv-cache uv run python scripts/sync_elevenlabs_agent.py --check

agent-sync:
	UV_CACHE_DIR=.uv-cache uv run python scripts/sync_elevenlabs_agent.py --apply

agent-route-test:
	UV_CACHE_DIR=.uv-cache uv run python scripts/test_elevenlabs_agent_routing.py

agent-acceptance-test:
	UV_CACHE_DIR=.uv-cache uv run python scripts/test_elevenlabs_agent_routing.py

smoke:
	UV_CACHE_DIR=.uv-cache uv run python scripts/smoke_live_sources.py

quality: lint typecheck test frontend frontend-verify
