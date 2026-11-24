# Backend targets. The root Makefile calls lint, test and build.
.PHONY: lint test test-db build seed-dev replay realtime-smoke eval-briefs eval-incall eval-incall-report eval-incall-history eval-incall-publish

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

seed-dev: ## local only: create user alex@example.test, a profile, a brief and a call; prints CALL_ID and ACCESS_TOKEN
	uv run python -m app.dev.seed

FIXTURE ?= tests/fixtures/vapi/happy_booking.jsonl

replay: ## post a recorded Vapi conversation to a running backend: make replay FIXTURE=... CALL_ID=<uuid>
	uv run python -m app.dev.replay $(FIXTURE) $(CALL_ID)

realtime-smoke: ## seeds Alex and Bob, starts uvicorn, checks Realtime delivery and isolation (needs Realtime)
	./scripts/realtime_smoke.sh

eval-briefs: ## LIVE DeepSeek brief eval (costs money, ~11 API calls); needs DEEPSEEK_API_KEY in the environment. Not in CI.
	uv run python -m evals.briefs.run

ARGS ?=
eval-incall: ## LIVE in-call model eval (costs ~$0.09, 195 runs); needs DEEPSEEK_API_KEY. ARGS="--cases C4- --runs 1". Not in CI.
	uv run python -m evals.incall.run $(ARGS)

eval-incall-report: ## write report.md for one in-call eval after judging: make eval-incall-report DIR=evals/incall/results/<time>
	uv run python -m evals.incall.report $(DIR)

eval-incall-history: ## one HTML page of every in-call eval run, by date: evals/incall/results/history.html
	uv run python -m evals.incall.history

eval-incall-publish: ## rebuild the history page and push it to the gh-pages branch (GitHub Pages). Run after judging.
	uv run python -m evals.incall.publish
