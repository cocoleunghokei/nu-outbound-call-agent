# Backend

The server side of the call agent: placing calls through a telephony service, running the speech-to-text → LLM → text-to-speech loop, pausing to ask the user, and the API the web app talks to.

The stack hasn't been picked yet (that's Nov 2–6). Add code here once the tech doc is done.

When code lands, add a `Makefile` here with `lint`, `test` and `build` targets so `make check` and CI run them. See [docs/devops.md](../docs/devops.md#plugging-in-the-stack).

## Call lock order

Any transaction that touches a call's `pings`, `call_secrets`, `outbox` or `call_events` calls `app.calls_lock.lock_call(conn, call_id)` first, so the `calls` row is always locked before the others. `app.events.append_event` re-locks the same row (its `last_seq` bump), which adds no second lock. Write `call_secrets` only through `write_call_secrets`, and end a call only through `end_call_transition`. `test_end_vs_reply_no_deadlock` guards this order.

## Vapi webhook ingress and handler registries

Every Vapi server message enters through `POST /webhooks/vapi` (`app/webhooks/vapi.py`). Owners plug in from their package's `register(reg)`:

- `reg.tools.register_tool_handler(name, fn)`, where `fn(conn, ctx: ToolCallContext) -> str`. `ask_user` and `report_decision` belong to Coco, `report_outcome` to Harper. A tool with no handler is stored `pending` in `tool_calls` and answered `"received"`; `dispatch_pending(pool, tools=reg.tools)` runs it later (at startup, and from the sweeper).
- `reg.events.register_event_handler(vapi_type, fn)`, where `fn(conn, call: BoundCall, redacted_body) -> None`. `status-update` and `speech-update` belong to Coco, `end-of-call-report` to Harper. A type with no handler is kept in `vapi_raw_events` only. `transcript` and `tool-calls` are handled by the ingress itself.

Handlers run inside the ingress transaction, in their own savepoint, with the call lock already held. They must not commit, must not call the network (500 ms ack budget; use the outbox), and must never store `confidence` or `dimensions`: `ctx.arguments` has them removed, and the ingress writes them to `agent_scores`. A handler that raises leaves its receipt `failed` plus a `call_events_failed` row, and a redelivery never reruns it.

Anything stored or logged goes through `app.webhooks.redact.redact` / `redact_text`. Every log line passes through `RedactingFormatter` (installed on the root and uvicorn handlers at startup), which scrubs the process-wide `SecretRegistry`: settings secrets plus every capability URL seen since startup. Never log a raw Vapi body.
