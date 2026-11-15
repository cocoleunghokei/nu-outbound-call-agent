# Outbound Call AI Agent

NU Launch Labs · Team: Coco (lead), Harper, Steve, Siddhi, Kevin · Demo: early December 2026

A web app that places phone calls for you, such as booking or cancelling a restaurant reservation or a doctor's appointment. It starts each call with your context. When it's missing information it **pauses instead of hanging up** and pings you for a one-tap reply, with human-in-the-loop (HITL) controls throughout.

See the kickoff one-pager: [docs/Outbound_Call_Agent_Kickoff_OnePager.pdf](docs/Outbound_Call_Agent_Kickoff_OnePager.pdf)

Product requirements: [docs/requirements.md](docs/requirements.md) (a snapshot of the Notion PRD, which stays the master copy).
System design: start with the [system overview](docs/system-overview.md) (diagrams), then [docs/architecture.md](docs/architecture.md) (blueprint) and [docs/tech-doc-during-call.md](docs/tech-doc-during-call.md) (During-call tech doc).

## Status

Build week (Nov 9–13). Each screen works with fake data on the [live site](https://nu-outbound-call-agent-frontend.vercel.app) (`?screen=precall|live|history`), and most of the during-call backend is built. The app doesn't place real calls yet. See [docs/status.md](docs/status.md) for what's built, what's missing, and why the live site is still a demo.

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
