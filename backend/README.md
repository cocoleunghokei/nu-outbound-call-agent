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

`make realtime-smoke` is the automated version of that check: it seeds Alex and Bob, starts uvicorn, subscribes to `call_events` for Alex's call with each user's token, replays one final transcript, and asserts that Alex receives it within 2 s and Bob receives nothing within 3 s. It needs a stack with Realtime (CI runs it after `supabase start`).

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

- `reg.tools.register_tool_handler(name, fn)`, where `fn(conn, ctx: ToolCallContext) -> str`. `ask_user` and `report_decision` belong to Coco, `report_outcome` to Harper. A tool with no handler is stored `pending` in `tool_calls` and answered `"received"`; `dispatch_pending(pool, tools=reg.tools)` runs it later (at startup, and from the sweeper).
- `reg.events.register_event_handler(vapi_type, fn)`, where `fn(conn, call: BoundCall, redacted_body) -> None`. `status-update` and `speech-update` belong to Coco, `end-of-call-report` to Harper. A type with no handler is kept in `vapi_raw_events` only. `transcript` and `tool-calls` are handled by the ingress itself.

Handlers run inside the ingress transaction, in their own savepoint, with the call lock already held. They must not commit, must not call the network (500 ms ack budget; use the outbox), and must never store `confidence` or `dimensions`: `ctx.arguments` has them removed, and the ingress writes them to `agent_scores`. A handler that raises leaves its receipt `failed` plus a `call_events_failed` row, and a redelivery never reruns it.

Anything stored or logged goes through `app.webhooks.redact.redact` / `redact_text`. Every log line passes through `RedactingFormatter` (installed on the root and uvicorn handlers at startup), which scrubs the process-wide `SecretRegistry`: settings secrets plus every capability URL seen since startup. Never log a raw Vapi body.
