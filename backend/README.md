# Backend

Python 3.12 + FastAPI, managed with uv. Plain SQL through psycopg 3, no ORM. It receives Vapi webhooks, runs the pause-and-ping loop and serves the web app's API. Start with the [system overview](../docs/system-overview.md) for diagrams. The design is in [docs/architecture.md](../docs/architecture.md); the wire shapes are in [contracts/](../contracts).

## Run it locally

```bash
supabase start                      # repo root: Postgres, Auth, Realtime
cd backend && uv sync
cp .env.example .env                # fill DATABASE_URL, SUPABASE_URL, SUPABASE_JWT_SECRET, VAPI_WEBHOOK_SECRET (any local string)
make seed-dev                       # fake user alex@example.test, profile, brief and a call; prints CALL_ID and ACCESS_TOKEN
uv run uvicorn app.main:app --reload
make replay CALL_ID=<the CALL_ID printed by seed-dev>    # in a second terminal
```

`make seed-dev` (`app/dev/seed.py`) refuses any database that is not on localhost or 127.0.0.1, and re-running it resets that user's seeded call. `make replay` (`app/dev/replay.py`) posts `tests/fixtures/vapi/happy_booking.jsonl` (a status update, six final transcripts, an `ask_user` call and an end-of-call report) to `http://localhost:8000/webhooks/vapi` with the secret from `VAPI_WEBHOOK_SECRET`, after rewriting each body's `metadata.call_id`. Pass `FIXTURE=` to replay another file. Replaying twice is safe: duplicates are dropped.

**What replay proves:** ordered `transcript.final` events arrive over Realtime, and the `ask_user` call lands as a `pending` row in `tool_calls`. Pings, status changes and outcomes appear only once their owners register handlers.

`make realtime-smoke` is the automated version of that check: it seeds Alex and Bob, starts uvicorn, subscribes to `call_events` for Alex's call with each user's token, replays one final transcript, and asserts that Alex receives it within 2 s and Bob receives nothing within 3 s. It needs a stack with Realtime (CI runs it after `supabase start`) and Node.js 22+, because `@supabase/realtime-js` uses Node's native WebSocket; on an older Node the script exits 2 with a clear message.

The smoke waits for Realtime's `postgres_changes` confirmation (a `system` message that can arrive after `SUBSCRIBED`) and fails on any subscription error Realtime reports, so a rejected subscription shows Realtime's own message instead of a timeout. It passes each user's token through the client's `accessToken` option. `client.realtime.setAuth(token)` is not enough: supabase-js keeps its own token callback, which returns the anon key when there is no session, and Realtime then re-subscribes as `anon` and fails (`invalid column for filter call_id`, because `anon` has no SELECT on `call_events`). If it fails in CI, the job prints the Realtime container's log.

## The patterns, by example

| To do this | Copy this |
| --- | --- |
| Auth + database route | `app/routers/me.py` |
| Idempotent mutation (`Idempotency-Key`) | `app/idempotency.py` |
| Write a call event | `app/events.py` (`append_event`) |
| Add a webhook handler | `app/webhooks/registry.py` |
| Use the voice platform | `app/voice/port.py` (`FakeVoicePlatform` in `app/voice/fake.py` for tests) |

## Ownership map

Each owner works in their own package. A package exposes `register(registries)` in its `__init__.py`; `create_app` finds it automatically, so merges don't conflict.

`register(reg)` runs before the database pool exists, so it only records things. Anything that needs the pool or settings goes in a lifecycle hook, which receives an `AppContext(pool, settings, registries)` (`app/registry.py`; `pool` is None when no `DATABASE_URL` is set):

- `reg.add_router(router)`: mount an API router.
- `reg.on_startup(fn)`: `async fn(ctx)` runs once at startup, after the pool opens and after the built-in startup (load `call_secrets`, run `dispatch_pending`). It must return; an exception stops the app from starting.
- `reg.add_worker(fn)`: `async fn(ctx)` runs as a background task for the life of the app, started after the startup hooks. Use it for loops (Coco's sweeper and outbox worker, Steve's notifier, Harper's jobs lane). On shutdown it is cancelled, so let `asyncio.CancelledError` propagate. A worker that raises is logged (redacted) and stops; the app keeps serving, so catch and log per iteration if the loop must survive errors.
- `reg.on_shutdown(fn)`: `async fn(ctx)` runs on shutdown after every worker has stopped and before the pool closes. Its exceptions are logged and the other hooks still run.

```python
async def sweeper(ctx: AppContext) -> None:
    while True:
        if ctx.pool is not None:
            await dispatch_pending(ctx.pool, tools=ctx.registries.tools)
        await asyncio.sleep(5)


def register(reg: Registries) -> None:
    reg.add_worker(sweeper)
```

| Owner | Package | Requirements | Builds on |
| --- | --- | --- | --- |
| Steve | `app/profile/`, `app/notifier/` | ON-3, ON-5 to ON-8, Web Push | `profile_fields`, `push_subscriptions`, outbox `notify` lane |
| Lotto | `app/briefs/` | PC-1 to PC-7 | `briefs`, `create-call` contract |
| Coco | `app/calls/` (controller, ask manager, status machine, outbox worker, sweeper) | DC-* | voice port, `call_secrets`, `pings`, outbox `control` lane, the `ask_user`, `report_decision`, `status-update` and `speech-update` handlers |
| Harper | `app/postcall/` | PO-1 to PO-6 | the `report_outcome` and `end-of-call-report` handlers, `shared_fields`, outbox `jobs` lane |

## Contract changes

Edit `contracts/`, run `make -C contracts gen`, and commit both generated files (`backend/app/contracts/generated.py` and `frontend/src/contracts/generated.ts`) in the same PR. Changes are additive only. Never hand-edit a generated file.

## Call lock order

Any transaction that touches a call's `pings`, `call_secrets`, `outbox` or `call_events` calls `app.calls_lock.lock_call(conn, call_id)` first, so the `calls` row is always locked before the others. `app.events.append_event` re-locks the same row (its `last_seq` bump), which adds no second lock. Write `call_secrets` only through `write_call_secrets`, and end a call only through `end_call_transition`. `test_end_vs_reply_no_deadlock` guards this order.

## Vapi webhook ingress and handler registries

Every Vapi server message enters through `POST /webhooks/vapi` (`app/webhooks/vapi.py`). Owners plug in from their package's `register(reg)`:

- `reg.tools.register_tool_handler(name, fn)`, where `fn(conn, ctx: ToolCallContext) -> str`. `ask_user` and `report_decision` belong to Coco, `report_outcome` to Harper. A tool with no handler is stored `pending` in `tool_calls` and answered `"received"`; `dispatch_pending(pool, tools=reg.tools)` runs it later (at startup, and from the sweeper worker, which reads the pool and registry from its `AppContext`).
- `reg.events.register_event_handler(vapi_type, fn)`, where `fn(conn, call: BoundCall, redacted_body) -> None`. `status-update` and `speech-update` belong to Coco, `end-of-call-report` to Harper. A type with no handler is kept in `vapi_raw_events` only. `transcript` and `tool-calls` are handled by the ingress itself.

Handlers run inside the ingress transaction, in their own savepoint, with the call lock already held. They must not commit, must not call the network (500 ms ack budget; use the outbox), and must never store `confidence` or `dimensions`: `ctx.arguments` has them removed, and the ingress writes them to `agent_scores`. **Tool handlers run once.** A tool handler that raises leaves its receipt `failed` plus a `call_events_failed` row, and a redelivery never reruns it. The exception is a transient database error (`psycopg.OperationalError`: lock timeout, query cancel, deadlock, serialization failure, lost connection): it is re-raised, the whole delivery rolls back (receipt included) and Vapi gets a 503 and retries; in `dispatch_pending` the row stays `pending` for the next run.

**Event handlers must be idempotent.** The receipt rule above is for tool handlers only. An event handler (`status-update`, `speech-update`, `end-of-call-report`) has no receipt: it runs again on every redelivery of the same message, and one that raises is dead-lettered into `call_events_failed` and never retried. So write it to be safe to run twice: pass a `dedup_key` to `append_event` (for example `status:<status>`, so a repeated status appends nothing), and make every UPDATE guarded (`... where status = 'talking'`, `... where state = 'open'`) so a second run changes nothing. End a call only through `end_call_transition`, which already does nothing for a terminal call.

Anything stored or logged goes through `app.webhooks.redact.redact` / `redact_text`. Every log line passes through `RedactingFormatter` (installed on the root and uvicorn handlers at startup), which scrubs the process-wide `SecretRegistry`: settings secrets plus every capability URL seen since startup. Never log a raw Vapi body.

## During call (`app/calls/`, Coco)

Call creation (`POST /calls`, PC-6 -> DC-3), the pause-and-ping loop (DC-6 to DC-9), Pause / Resume / End (DC-11), the call status machine (DC-14) and decision logging (DC-4).

**Placing real calls** needs `VOICE_PLATFORM=vapi`, `VAPI_API_KEY`, `VAPI_PHONE_NUMBER_ID`, `VAPI_WEBHOOK_SECRET` and `PUBLIC_BASE_URL` (this backend's public origin, which must be `https://`, such as the Railway URL; Vapi posts webhooks to `{PUBLIC_BASE_URL}/webhooks/vapi`). Without them `POST /calls` answers `503 calls_not_configured`, writes nothing and dials nothing. With the default `VOICE_PLATFORM=fake`, nothing is ever dialed.

**Who may place a call:** every route, `POST /calls` included, takes any signed-in Supabase user through `app/auth.py:current_user`, anonymous sessions included (the browser signs in silently on first use and collects the profile on the first visit, ON-1 interim). What gates a call is the brief's readiness against the real profile (`422 brief_not_ready` names the missing keys; `calling_name` is always required) and the destination allowlist below. `current_permanent_user` (anonymous answers `403 sign_in_required`) exists for a later email sign-in and no route uses it today.

**Limiting who gets called:** `CALL_DESTINATION_ALLOWLIST` (a JSON list of US numbers, like `CORS_ORIGINS`) makes `POST /calls` refuse any other number with `422 destination_not_allowed` before a row is written. Entries are normalised to E.164 at startup, and an entry that isn't a dialable US number stops the app from starting. It is a stopgap while the per-user call caps (DC-15) don't exist yet (the anon key is public, so anyone can create an email-verified account): set it to the team's test phones in production, and leave it empty (no restriction) once the caps land.

| File | What it does |
| --- | --- |
| `ask.py` | `ask_user` tool handler. Opens a ping with a 45 s clock (frozen if the call is paused), moves the call to `waiting_on_you`, queues the push (`notify` lane) and two "still checking" lines at 15 s and 30 s. An Ask-me-first `field_requested` becomes a `field_approval` ping with a server-written question and Share / Don't share. A newer question (by Vapi timestamp, then tool call id) replaces the open one; an older one is stored `superseded` and never shown. |
| `reply.py` | `POST /calls/{call_id}/pings/{ping_id}/reply` (`{"choice": n}` or `{"text": "..."}`, `Idempotency-Key` required). Wins only if the ping is open and `clock_timestamp() < expires_at` under the ping's row lock, else `409 ping_closed`. Free text is cleaned and capped at 200 characters, and is refused on a `field_approval` ping: only Share releases a field, and its value goes only into the agent's `add-message`, never into an event. |
| `commands.py` | `POST /calls/{call_id}/commands` (`pause`, `resume`, `end`; `instruction` answers 422 until DC-10 lands). Pause: `on_hold` + `paused_at`, freezes an open ping (`expires_at` null, `remaining_ms` kept, re-announced as `ping.opened`), queues the hold line, a mute (timed by the quiet rule in `speech.py`, at the latest 4 s) and a reassurance every 15 s (unmute, say, mute). Resume: unmutes, restarts the ping clock where it stopped, re-queues held answers after the unmute. End (and the pause cap): cancels the open ping and every pending control row, queues the goodbye with `endCallAfterSpoken` (unmuting first if paused) and `end-call` 6 s later; after that nothing else is sent and `ask_user` is refused. Repeating a command is a no-op. |
| `create.py` | `POST /calls {brief_id}` (`Idempotency-Key` required). Locks the brief and checks it again: the contract shape, the PC-7 number rules, readiness against the profile now, the assistant config's safety check (`422 unsafe_assistant_config`, before any row exists), and no other call from this brief still running (`409 call_in_progress`). Commits the call as `creating` with its profile snapshot and `create_attempted_at`, then sends exactly one create request with our `call_id` in the Vapi metadata. Placed: stores `vapi_call_id` and the control URL, `creating -> dialing`. Provably not placed (4xx, no connection): `failed`, and the brief can be started again. Unknown (timeout, 5xx): `creation_uncertain`, never resent; webhooks still bind by `call_id`, and `reconcile.py` settles it. The result and the final idempotent response are written in one transaction. Answers `201 {call_id, status}`; a replay of the key returns the same call. |
| `reconcile.py` | Settles a call whose creation had no clear answer, so it never blocks its brief (every 10 s, from the sweeper). `creating` with no `vapi_call_id` 15 s after `create_attempted_at` (a crash mid-request) -> `creation_uncertain`. `creation_uncertain`: looked up at Vapi by our `call_id`; found and already ended at Vapi (its end webhooks were lost) -> ended, with the `outcome` and `shared_fields` jobs queued as the lost report would have (Vapi's `endedReason`, no report transcript); found and still running -> adopted (`dialing`), and if found after 2 min (the screen already said failed) the agent apologises and hangs up; not found by `max_duration_s` + 5 min -> `failed`. Never resends the create request. |
| `status.py` | `status-update` handler: `queued`/`ringing` -> `dialing`, `in-progress` -> `talking` (only before the call connected), `ended` -> `end_call_transition`. Never overrides `waiting_on_you` or `on_hold`. |
| `speech.py` | `speech-update` handler, the quiet rule: after the hold line is sent, the mute goes out once the assistant has been silent for 0.7 s after an assistant `stopped`; an assistant `started` pushes it back to its 4 s deadline. |
| `decisions.py` | `report_decision` handler: writes `agent.decision` (situation, action, reason, `field_key`, `missed_preference`), deduplicated per tool call. |
| `sweeper.py` | Worker: every 0.5 s expires open pings whose clock ran out (queues the "did not reply" message) and ends calls paused longer than 2 min with a call-back offer (the outcome is left to post-call for now); every 5 s runs `dispatch_pending`. |
| `outbox.py` | Worker: sends due `control` rows per call in id order via the voice port, outside any transaction. Fillers are dropped if their ping closed or the call is paused, pause lines after Resume, and everything but the goodbye once the call is ending; fillers and reassurances are never retried. Replies and timeouts wait while the call is paused. `VoiceNotSent` is retried once; `VoiceUncertain` becomes `uncertain` and is never resent. On start-up a pending row that may have been sent becomes `uncertain`. Assumes one backend instance. |
| `prompt.py` | The agent's system prompt from the brief and the profile snapshot: share-freely values, ask-first field names (never values), constraints, preferences, ranked fallbacks, never-agree items, the DC-4 rubric (2 gates, 4 checks, 3 tiers), how to wait for the user and the no-reply rule. `GUARDRAIL_RULES` is a placeholder for DC-13. |
| `assistant.py` | `build_assistant_config`: the DC-1 opening line (`voice/prompt_safety.build_first_message`), the prompt, recording off and transcripts on, the webhook URL and secret header, the max duration and the model (DeepSeek-V3; its key is set in the Vapi dashboard). Runs `assert_prompt_safe` over both texts. Pure; call creation will use it. |
| `messages.py` | Every sentence put into a call, in one place to tune from test calls. |
| `store.py`, `pings.py` | Shared helpers: profile snapshot reads, guarded status changes, outbox writes, ping open/close. |

Tests: `tests/test_calls_*.py` (`calls_helpers.py` seeds a call with a snapshot and a control URL). They need the local Supabase stack, like the other `db` tests. `test_calls_e2e.py` runs the real workers and checks the reply is handed to the voice adapter within 2 s.

## Post-call (`app/postcall/`, Harper)

The end of a call: the outcome (PO-1) and what was spoken (PO-3). The summary (PO-2, LLM) and the closed-tab call-ended push (PO-5) come next.

| File | What it does |
| --- | --- |
| `handlers.py` | `report_outcome` tool handler: acknowledges; the arguments stay on the `tool_calls` receipt for the outcome job. `end-of-call-report` handler: `end_call_transition` (a no-op if `status-update: ended` came first), then queues the `outcome` and `shared_fields` jobs on the `jobs` lane, at most one of each per call, with the report's own transcript (`agent_lines`, `business_lines`) in the payload. The reconciler also queues them, without a transcript, for a call it finds already ended at Vapi. |
| `jobs.py` | Worker: every 2 s runs due `jobs` rows under the call lock. Evidence is the `transcript.final` events plus the report's transcript, and a still-`pending` `report_outcome` receipt counts. `outcome` writes `calls.outcome` once (`coalesce`) and then appends `call.ended {outcome, ended_reason}` with `dedup_key = "call.ended"`, so `call.ended` goes out once, after the outcome is known. `shared_fields` marks each identifying field the agent said as `spoken` and appends `field.shared`. A failing job retries with backoff (2 s doubling to 60 s) and is `failed` after 5 attempts; when the `outcome` job gives up, `needs_follow_up` and `call.ended` are written in the same transaction. |
| `history.py` | `GET /calls` (the caller's calls with their brief, newest first, at most 100; no paging yet) and `GET /calls/{call_id}/result` (the call, the latest `report_outcome`'s `business_confirmed` and details only when the outcome is booked, cancelled or rescheduled and matches that report, and then only the details the business said in the shown transcript (`shown_details` → `decide.confirmed_details`: each of date, time, confirmation number and staff name checked on its own, with the same question and refusal rules; a made-up detail is left out), its open items, `agent.decision` events, `shared_fields`, and `transcript.final` lines in spoken order: `occurred_at`, then `seq`). 404 for a call that isn't the caller's; the brief join also checks the brief's owner. Through the backend because `tool_calls` is backend-only; every query filters by the caller. |
| `decide.py` | The pure rules; when in doubt, Needs follow-up. Booked, Cancelled or Rescheduled need `business_confirmed`, well-formed details (an ISO date, a time that parses, a confirmation number of 4+ letters or digits; checked here, not in the contract, so one odd field doesn't throw away the whole report), and one business clause that says the confirmation number next to a cue ("confirmation", "reference", "number is", ...) and not inside a longer number, or the agreed time and date (month and day, and the year if one is said) together. A weekday ("Thursday", "this Thursday") also names the date when it means the agreed date 1 to 6 days after the call, for every US time zone the call could be in (`call_days_for`, from `calls.created_at` and `ended_at`); never the call day's own weekday, "next/last Thursday", "next week", or a clause that also names another month and day. A whole hour also counts as "at 2" with no am/pm. Any "?" ends a clause, and so does ".", "!" or ";" before a capital ("Oct." and "p.m." stay whole); a question is never evidence, and a line where any clause refuses ("full", "sorry", "not", "let me check", "call you back", "Wait, no", ...) is never evidence, nor is anything said before a later refusal line. So a refusal and a counteroffer in one line is Needs follow-up. Known limit: an offer ("How about October 24 at 7:30.") reads like a confirmation; `business_confirmed` guards it. An am/pm that contradicts the agreed time doesn't count, and "7:30" never matches inside "17:30". No `report_outcome`: `customer-did-not-answer`, `customer-busy` or `voicemail` is No answer, anything else Needs follow-up (never a guessed Failed). A pause-cap ending (DC-11) with no report is Needs follow-up. Spoken check: only identifying fields (`SPOKEN_FIELD_KEYS`); a date of birth also as "April 12, 1990" or "04/12/1990"; an address by its street part with "St"/"Street" alike; phone numbers one spoken number at a time by their last 10 digits; names as whole words, and names that are ordinary words ("May") only after a cue like "on behalf of". |

Not yet: the summary job, the 2-minute `GET /call` fallback when no `end-of-call-report` arrives, setting `sent_to_agent`, and a column for the outcome reason (it is only logged).

Tests: `tests/test_postcall_decide.py` (pure), `tests/test_postcall_db.py` and `tests/test_postcall_history.py` (`db`). `tests/test_webhooks.py` uses `foundation_app()` (no owner packages) where it needs `report_outcome` to have no handler.
