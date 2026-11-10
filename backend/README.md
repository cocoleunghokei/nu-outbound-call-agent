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

The pause-and-ping loop (DC-6 to DC-9), Pause / Resume / End (DC-11), the call status machine (DC-14) and decision logging (DC-4). Call creation (`POST /calls`) comes next, once the pre-call handoff is settled.

| File | What it does |
| --- | --- |
| `ask.py` | `ask_user` tool handler. Opens a ping with a 45 s clock (frozen if the call is paused), moves the call to `waiting_on_you`, queues the push (`notify` lane) and two "still checking" lines at 15 s and 30 s. An Ask-me-first `field_requested` becomes a `field_approval` ping with a server-written question and Share / Don't share. A newer question (by Vapi timestamp, then tool call id) replaces the open one; an older one is stored `superseded` and never shown. |
| `reply.py` | `POST /calls/{call_id}/pings/{ping_id}/reply` (`{"choice": n}` or `{"text": "..."}`, `Idempotency-Key` required). Wins only if the ping is open and `clock_timestamp() < expires_at` under the ping's row lock, else `409 ping_closed`. Free text is cleaned and capped at 200 characters, and is refused on a `field_approval` ping: only Share releases a field, and its value goes only into the agent's `add-message`, never into an event. |
| `commands.py` | `POST /calls/{call_id}/commands` (`pause`, `resume`, `end`; `instruction` answers 422 until DC-10 lands). Pause: `on_hold` + `paused_at`, freezes an open ping (`expires_at` null, `remaining_ms` kept, re-announced as `ping.opened`), queues the hold line, a mute (timed by the quiet rule in `speech.py`, at the latest 4 s) and a reassurance every 15 s (unmute, say, mute). Resume: unmutes, restarts the ping clock where it stopped, re-queues held answers after the unmute. End (and the pause cap): cancels the open ping and every pending control row, queues the goodbye with `endCallAfterSpoken` (unmuting first if paused) and `end-call` 6 s later; after that nothing else is sent and `ask_user` is refused. Repeating a command is a no-op. |
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
| `handlers.py` | `report_outcome` tool handler: acknowledges; the arguments stay on the `tool_calls` receipt for the outcome job. `end-of-call-report` handler: `end_call_transition` (a no-op if `status-update: ended` came first), then queues the `outcome` and `shared_fields` jobs on the `jobs` lane, at most one of each per call. |
| `jobs.py` | Worker: every 2 s runs due `jobs` rows under the call lock. `outcome` writes `calls.outcome` once (guarded `outcome is null`) and then appends `call.ended {outcome, ended_reason}` with `dedup_key = "call.ended"`, so `call.ended` goes out once, after the outcome is known. `shared_fields` marks each snapshot field the agent said as `spoken` and appends `field.shared`. A failing job retries with backoff (2 s doubling to 60 s) and is `failed` after 5 attempts; the two jobs never block each other. |
| `decide.py` | The pure rules. Booked, Cancelled or Rescheduled need `business_confirmed` and the agreed time, date or confirmation number in the business's own words; else Needs follow-up. No `report_outcome`: `customer-did-not-answer`, `customer-busy` or `voicemail` is No answer, anything else Needs follow-up (never a guessed Failed). A pause-cap ending (DC-11) with no report is Needs follow-up. Spoken check: phone numbers by their last 10 digits, other values as whole words. |

Not yet: the summary job, the 2-minute `GET /call` fallback when no `end-of-call-report` arrives, setting `sent_to_agent`, and a column for the outcome reason (it is only logged).

Tests: `tests/test_postcall_decide.py` (pure) and `tests/test_postcall_db.py` (`db`). `tests/test_webhooks.py` uses `foundation_app()` (no owner packages) where it needs `report_outcome` to have no handler.
