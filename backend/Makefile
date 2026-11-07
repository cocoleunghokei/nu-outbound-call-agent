# Backend targets. The root Makefile calls lint, test and build.
.PHONY: lint test test-db build

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy --strict app

test:
	uv run pytest -m "not db"

test-db: ## needs the local Supabase stack
	uv run pytest -m db

build:
	uv run python -c "import app.main"
