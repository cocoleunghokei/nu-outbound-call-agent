# Agent guide

Instructions for every AI coding agent (Claude Code, Codex, Cursor, Copilot, ...) and every human working in this repo. Read this before changing anything. Keep it accurate: if you change a convention, update this file in the same PR.

## Project in one paragraph

A web app that places phone calls for the user, such as booking or cancelling a restaurant reservation or a doctor's appointment. When the agent is missing information mid-call it pauses and pings the user instead of hanging up. See [README.md](README.md) for scope and timeline, and [docs/devops.md](docs/devops.md) for how code gets from a branch to production.

## Current stack (inspect before assuming)

| Area | Status |
| --- | --- |
| Database | Supabase (Postgres 17). Schema lives only in `supabase/migrations/`. |
| Backend (`backend/`) | Not picked yet (tech doc, Nov 2–6). No code. |
| Frontend (`frontend/`) | Not picked yet (tech doc, Nov 2–6). No code. |
| CI/CD | GitHub Actions in `.github/workflows/`, driven by the root `Makefile`. |

Before writing code, read the manifests that exist (`package.json`, `pyproject.toml`, lockfiles), the migrations and any model definitions. Use the package manager and libraries already in the tree. Do not add a dependency for something an existing one already does. When the stack is picked, update the table above.

## Commands

```bash
make setup      # once per clone: install the git hooks
make check      # everything CI runs except the database check; run before every push
make db-check   # rebuild the local DB from migrations, lint it, check RLS (needs Docker)
make help       # list all targets
```

CI calls these same targets, so passing locally means passing in CI.

## Rules

### Verify before you finish
- Run `make check` (and `make db-check` if you touched `supabase/`) before declaring a task done. Fix every error or warning your change introduces.
- Never bypass hooks (`--no-verify`) or weaken a check to make it pass.
- Report results honestly. If you could not run a check, say so.

### Keep changes atomic
- One task per branch and PR. Do not reformat, rename or refactor unrelated code; it causes merge conflicts with other agents' PRs.
- Treat existing public APIs, exported types, table and column names as contracts. Change them only when the task requires it, and update every caller in the same PR.

### Commits and PRs
- [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): summary`. Types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`, `chore`, `revert`. Scopes: `backend`, `frontend`, `db`, `ci`, `docs`.
- PRs are squash-merged, so the PR title must also follow the format. CI checks it.

### Code
- Strict typing and explicit interfaces. Validate input at every boundary: HTTP handlers, webhooks (telephony callbacks), LLM tool calls, and database writes.
- Self-explanatory names and docstrings on public functions. Avoid clever one-liners; other agents will read and refactor your code.
- Every bug fix ships with a regression test. Every feature ships with tests that follow the existing test patterns.

### Database
- Change the schema only through a new file from `supabase migration new <name>`. Never edit a migration that has been merged; add a new one.
- Every table in `public` gets `alter table ... enable row level security;` in the migration that creates it. `make db-check` fails otherwise.
- Postgres identifiers are `snake_case`. Primary keys are `uuid`, matching Supabase `auth.users.id`, unless the tech doc decides otherwise.
- Billing and audit data (call minutes, call status, outcomes) is written only by the backend, never by the browser.

### Secrets and personal data
- Never commit API keys, tokens or passwords. Secrets go in `.env` (git-ignored); variable names go in `.env.example`. The gitleaks hook and CI block known key formats, but they are a backstop, not a licence.
- Use fake personal data in tests, fixtures and seeds. Never commit real names, birthdays, addresses, insurance details or call recordings.

## Where context lives

| What | Where |
| --- | --- |
| Product scope, timeline | `README.md`, `docs/Outbound_Call_Agent_Kickoff_OnePager.pdf` |
| Product requirements and their IDs (`ON-`, `PC-`, `DC-`, `PO-`) | `docs/requirements.md` (snapshot; Notion is the master copy) |
| System architecture blueprint (components, protocols, delivery rules) | `docs/architecture.md` |
| Branch and PR workflow, DB setup | `CONTRIBUTING.md` |
| CI/CD pipeline, required checks, deploys | `docs/devops.md` |
| Module-specific notes | `backend/README.md`, `frontend/README.md` (add edge cases and patterns there as code lands) |
