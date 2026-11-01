.PHONY: install dev test lint typecheck web-test web-build demo up down

PY := .venv/bin

install:  ## Create venv and install everything
	python3 -m venv .venv && $(PY)/pip install -e ".[dev]"
	cd web && npm ci

dev:      ## Run API (8000) and dashboard (5173) with hot reload
	$(PY)/uvicorn sentinel.api:app --reload --port 8000 & cd web && npm run dev

test:     ## Backend + frontend tests (set SENTINEL_TEST_PG_URL / _MYSQL_URL for more dialects)
	$(PY)/pytest
	cd web && npm test

lint:
	$(PY)/ruff check src tests && $(PY)/ruff format --check src tests
	cd web && npm run lint

typecheck:
	$(PY)/mypy
	cd web && npm run typecheck

demo:     ## Rebuild the demo clinic DB and simulate 14 days of runs
	$(PY)/sentinel demo --days 14

up:
	docker compose up --build -d && docker compose run --rm api sentinel demo
down:
	docker compose down -v
