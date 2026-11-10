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
