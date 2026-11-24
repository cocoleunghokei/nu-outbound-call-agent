# Outbound Call AI Agent

NU Launch Labs · Team: Coco (lead), Harper, Steve, Siddhi, Kevin · Demo: early December 2026

A web app that places phone calls for you, such as booking or cancelling a restaurant reservation or a doctor's appointment. It starts each call with your context. When it's missing information it **pauses instead of hanging up** and pings you for a one-tap reply, with human-in-the-loop (HITL) controls throughout.

See the kickoff one-pager: [docs/Outbound_Call_Agent_Kickoff_OnePager.pdf](docs/Outbound_Call_Agent_Kickoff_OnePager.pdf)

Product requirements: [docs/requirements.md](docs/requirements.md) (a snapshot of the Notion PRD, which stays the master copy).
System design: start with the [system overview](docs/system-overview.md) (diagrams), then [docs/architecture.md](docs/architecture.md) (blueprint) and [docs/tech-doc-during-call.md](docs/tech-doc-during-call.md) (During-call tech doc).

## Status

Build week (Nov 9–13). Each screen works with fake data on the [live site](https://nu-outbound-call-agent-frontend.vercel.app) (`/`, `/live`, `/history`), and most of the during-call backend is built. The app doesn't place real calls yet. See [docs/status.md](docs/status.md) for what's built, what's missing, and why the live site is still a demo.

## Repo layout

```
backend/              FastAPI app: briefs, calls (pause and ping), Vapi adapter, webhooks
frontend/             React + Vite web app: pre-call chat, live call, history, result
contracts/            JSON Schemas; generated Python and TypeScript types
supabase/migrations/  Database schema (the only place it changes)
spikes/vapi/          Vapi probe kit
docs/                 Requirements, system overview, architecture, status
```

Module by module: [docs/status.md#project-structure](docs/status.md#project-structure).

## How we work

- `main` is the shared branch. Keep it working, and don't push to it directly.
- Do your work on a branch off `main`, then open a pull request.
- See [CONTRIBUTING.md](CONTRIBUTING.md) for branch names and the PR flow.
- Run `make setup` once, and `make check` before you push. CI runs the same checks on every PR. See [docs/devops.md](docs/devops.md).
- AI agents follow [AGENTS.md](AGENTS.md).

### Pull requests (humans and AI agents, every time)

- **Title:** Conventional Commits, `type(scope): summary`, for example `feat(backend): add endpoint to start a call`. Scopes: `backend`, `frontend`, `db`, `ci`, `docs`, `spike`. CI checks it, and it becomes the commit on `main`.
- **Description:** fill in every heading of [the PR template](.github/pull_request_template.md): "What does this change?", "How did you test it?" and "Checklist". Do not leave a heading empty or replace the template with a commit message.
- **Testing section:** list the exact commands you ran and their results. Say plainly what you could not run (for example `make check` or `make db-check` without Docker) and what only CI will cover. Update the numbers if you merge `main` or push again.
- **Checklist:** tick only what is true. Leave `make check` and "A teammate has reviewed it" unticked until they are.
- **Before merging:** wait for every required check (`Hygiene and secret scan`, `Lint, test, build`, `Database migrations`, `Conventional Commits title`) to pass on the latest commit, and get one teammate's approval. Merge with **Squash and merge**.
- One task per branch and PR. If the PR touches docs or conventions, update them in the same PR.

## Timeline

| Phase | Dates |
| --- | --- |
| Research, design, spec | Oct 21–31 |
| Requirements + tech doc, pick the stack | Nov 2–6 |
| Build the core call + pause-and-ask loop | Nov 9–13 |
| Test + fix (real test calls) | Nov 16–20 |
| Feature-complete, demo prep | Nov 23–27 |
| Buffer | Nov 30–Dec 4 |
| Demo | Early Dec |
