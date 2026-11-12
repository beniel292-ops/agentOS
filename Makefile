.PHONY: setup dev test migrate down

setup:
	@test -f .env || cp .env.example .env
	cd apps/api && uv sync
	cd apps/web && pnpm install
	@test -L apps/web/.env.local || test -e apps/web/.env.local || ln -s ../../.env apps/web/.env.local

dev:
	docker compose up -d --wait
	@trap 'kill 0' INT TERM EXIT; \
	(cd apps/api && uv run uvicorn app.main:app --reload --port 8000) & \
	(cd apps/api && uv run arq app.jobs.worker.WorkerSettings) & \
	(cd apps/web && pnpm dev) & \
	wait

test:
	cd apps/api && uv run pytest -q && uv run ruff check .
	cd apps/web && pnpm lint

migrate:
	cd apps/api && uv run alembic upgrade head

down:
	docker compose down
