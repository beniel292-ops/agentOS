.PHONY: setup dev prod test migrate down drive-auth

setup:
	@test -f .env || cp .env.example .env
	@sh scripts/ensure_env_secrets.sh
	cd apps/api && uv sync
	cd apps/web && pnpm install
	@test -L apps/web/.env.local || test -e apps/web/.env.local || ln -s ../../.env apps/web/.env.local

dev:
	docker compose up -d --wait
	@trap 'kill 0' INT TERM EXIT; \
	(cd apps/api && uv run uvicorn app.main:app --reload --port 8000) & \
	(cd apps/api && uv run arq app.jobs.worker.WorkerSettings) & \
	(cd apps/api && uv run python -m app.telegram.bot) & \
	(cd apps/api && uv run python -m app.mcp_server) & \
	(cd apps/web && pnpm dev) & \
	wait

# Production style on this Mac: no --reload, built web, migrations first, and every long-running
# process restarts 5 s after it exits. Ctrl+C stops everything (kill 0 also stops the loops).
prod:
	docker compose up -d --wait
	$(MAKE) migrate
	cd apps/web && pnpm build
	@trap 'kill 0' INT TERM EXIT; \
	run() { while :; do sh -c "$$1"; echo "[prod] stopped: $$1 -- restarting in 5s" >&2; sleep 5; done; }; \
	run 'cd apps/api && exec uv run uvicorn app.main:app --host 127.0.0.1 --port 8000' & \
	run 'cd apps/api && exec uv run arq app.jobs.worker.WorkerSettings' & \
	run 'cd apps/api && exec uv run python -m app.telegram.bot' & \
	run 'cd apps/api && exec uv run python -m app.mcp_server' & \
	run 'cd apps/web && exec pnpm exec next start -p 3000 -H 127.0.0.1' & \
	wait

test:
	cd apps/api && uv run pytest -q && uv run ruff check .
	cd apps/web && pnpm lint

migrate:
	cd apps/api && uv run alembic upgrade head

down:
	docker compose down

drive-auth:
	cd apps/api && uv run python -m scripts.drive_auth
