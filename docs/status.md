# Project status

Snapshot of 2026-11-11 (`main` @ `eef65e7`). Build week: "build the core call + pause-and-ask loop" (Nov 9–13). Next week is real test calls (Nov 16–20).

**One line:** each screen works on its own with fake data, and most of the during-call backend is built and tested. Nothing connects the screens yet, and nothing in the app places a real phone call.

Update this page when a gap below closes, and link the PR.

## What the live site shows, and why it's still a demo

Site: https://nu-outbound-call-agent-frontend.vercel.app. Pick a screen with `?screen=precall|live|history`.

Every screen runs on fake data. `?screen=live` always plays the scripted "Brookline Family Medicine" call (`frontend/src/live-call/mockCall.ts`), even after you press **Start call** on the pre-call screen with your own number.

There are five independent reasons. Each one alone keeps the app in demo mode:

| # | Layer | What happens | Where | Fix tracked in |
|---|---|---|---|---|
| 1 | Routing | The screens aren't linked. `App.tsx` is a temporary `?screen=` switcher, so Start doesn't open the live page, and the live page doesn't know which call to follow. It shows a real call only with `?call=<uuid>`. | `frontend/src/App.tsx`, `LiveCallDemo.tsx` | #46 |
| 2 | Vercel build | Production is built without `VITE_API_URL` or `VITE_LIVE_CALL_SOURCE`. Vite inlines env vars at build time, so `liveCallMode()` returns `mock` and the pre-call screen picks `demoStarter`, which only logs `start call with brief (demo mode, nothing sent)`. | `live-call/backend.ts` `liveCallMode()`, `precall/mode.ts`, `precall/start.ts` | #51 |
| 3 | Sign-in | API mode also needs a signed-in Supabase session. There's no sign-in screen yet, so the pre-call screen falls back to demo with reason `no_session`. | `precall/mode.ts`; ON-1 | not filed yet |
| 4 | Backend route | `POST /calls {brief_id}` doesn't exist (it returns 404), so nothing calls `VapiVoice.create_call`. Even in API mode, Start saves the brief and reports "saved, not connected". | `backend/app/voice/vapi.py` exists; the route is missing | #45 |
| 5 | Backend deploy | The backend isn't deployed. There's no Railway URL to put in `VITE_API_URL`, the Vapi server URL isn't set, and the default CORS allows only `http://localhost:5173`. | `backend/app/config.py` (`CORS_ORIGINS`) | #51 |

The one real call so far (2026-11-10, about 40 s) was placed directly through the Vapi API with a generic dashboard assistant, not through the app. It proves the phone path, Twilio number → Vapi → a cell phone, and nothing more.

**Order to unblock a real call from the app:** #45 (`POST /calls`), then #51 (deploy and env vars), then sign-in, then #46 (Start → `/live?call=<id>`).

## What's built

**Platform (grey):**
- Supabase schema: 6 migrations, RLS on every table.
- FastAPI app with the registries, auth, idempotency and the error shape.
- Webhook ingress with signature check, normalising and redaction.
- Event log (`events.py`) and outbox.
- JSON Schema contracts with generated Python and TS types.
- CI in `.github/workflows/`.

**Telephony:**
- Twilio number +1 231-359-4180, imported into Vapi as a phone number.
- KYC approved 2026-11-10.
- The Vapi probe kit is in `spikes/vapi/`. `RESULTS.md` is still empty, and the go/no-go verdict is pending.

**Requirements:**

| Area (owner) | Built | Missing |
|---|---|---|
| Onboarding (Steve) | JWT auth (`backend/app/auth.py`), `GET /me` | Sign-in screen, profile API, notification permission (ON-1 to ON-7) |
| Pre-call (Lotto) | Chat UI (`frontend/src/precall/`), `/briefs` API (templates, build, create, get, patch) | Answer validation (#47, #48). DeepSeek opening line for custom calls is in review (PR #44). |
| During call (Coco) | `ask_user` pause and ping, reply, timeout sweeper, pause/resume/end via outbox, call status, decision logging, agent prompt and assistant config (`backend/app/calls/`). Live page with mock and real (Supabase Realtime) modes (`frontend/src/live-call/`). | `POST /calls` (#45), send an instruction (DC-10), browser push (DC-8) |
| Post-call (Harper) | History list and result page with fake data. Transcript view (#53). | Outcome handlers (PR #55), history API (PR #56), real data on screen (PR #57), notification (PR #54): all in review with changes requested. Back button (#49). |

**Tests on `main`:**
- Backend: 295 pass, with the database tests excluded (`make -C backend test`). The 136 database tests run in CI.
- Frontend: 348 pass.

## Open work

**Issues from the 2026-11-10 end-to-end QA:**

| Issue | Owner | Blocks a real call? |
|---|---|---|
| #45 `POST /calls` | Coco | yes |
| #46 connect the screens | Coco, Lotto, Harper | yes |
| #47 typed "none"/"yes" saved as hard limits | Lotto | no |
| #48 party size, day, time not validated | Lotto | no |
| #49 Back on history | Harper | no |
| #50 transcript (PO-4) | Harper | no. The fake-data half is merged; it waits on #56 and #57 for real data. |
| #51 deploy, CORS and env vars | Steve | yes |

**Open PRs:**
- #44 (PC-3 DeepSeek)
- #54, #55 and #56: review comments posted, waiting on fixes
- #57: waits on #54 and #56

**Known infra issue:** the Vercel check fails on PRs from authors who aren't on the Vercel team ("Git author … must have access to the project"). It doesn't block merging, but those PRs get no preview deploy.

## Project structure

```
backend/                 FastAPI app (Python 3.12, uv). See backend/README.md
  app/main.py            App factory; registries wire each owner's package in
  app/auth.py            Supabase JWT check
  app/briefs/            Pre-call: templates, brief builder, readiness, /briefs routes   (Lotto)
  app/calls/             During call: ask_user, pings, reply, commands, outbox, sweeper,
                         status, decisions, agent prompt + assistant config            (Coco)
  app/voice/             Voice port, Vapi adapter (create_call), fake adapter, tool schemas
  app/webhooks/          POST /webhooks/vapi: verify, normalise, redact, dispatch
  app/events.py          The only writer of public.call_events
  app/routers/me.py      GET /me                                                         (Steve)
  app/dev/               Local stack, seed and replay tools
  tests/                 pytest; `-m db` tests need local Supabase (Docker)
frontend/                React 19 + TypeScript + Vite, on Vercel. See frontend/README.md
  src/App.tsx            Temporary ?screen= switcher (to be replaced, #46)
  src/precall/           Chat home, review card, demo/API mode, Start                   (Lotto)
  src/live-call/         Live call page, ping card, controls, mock + real data          (Coco)
  src/history/           Call history list                                              (Harper)
  src/result/            Call result page, transcript                                   (Harper)
  src/lib/supabase.ts    The one Supabase client
  src/contracts/         Generated TS types (don't edit; run make -C contracts gen)
contracts/               JSON Schemas for briefs, events, pings, commands, tools, errors
supabase/migrations/     The schema (6 migrations; RLS on every table)
spikes/vapi/             Vapi probe kit for the go/no-go session
docs/                    Requirements, system overview, architecture, tech doc, this page
.github/workflows/       ci.yml, db-deploy.yml, pr-title.yml
```

## Configuration that decides demo vs real

| Variable | Where | Effect when empty |
|---|---|---|
| `VITE_API_URL` | Vercel (frontend) | Pre-call is demo; the live page is mock |
| `VITE_LIVE_CALL_SOURCE=real` | Vercel | Live page is mock, even with `?call=` |
| `VITE_HISTORY_SOURCE=real` | Vercel (after PR #57) | History is fake |
| `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` | Vercel | API mode can't sign in |
| `CORS_ORIGINS` / `CORS_ORIGIN_REGEX` | Railway (backend) | Browser calls from the Vercel origin are refused |
| `VAPI_API_KEY`, `VAPI_PHONE_NUMBER_ID`, `VAPI_WEBHOOK_SECRET`, `DATABASE_URL`, `SUPABASE_*` | Railway | No call can be placed, verified or stored |

Vite inlines `VITE_*` values at build time, so redeploy on Vercel after changing them. Variable names are in `frontend/.env.example` and `backend/.env.example`; values never go in git.
