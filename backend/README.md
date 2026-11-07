# Backend

The server side of the call agent: placing calls through a telephony service, running the speech-to-text → LLM → text-to-speech loop, pausing to ask the user, and the API the web app talks to.

The stack hasn't been picked yet (that's Nov 2–6). Add code here once the tech doc is done.

When code lands, add a `Makefile` here with `lint`, `test` and `build` targets so `make check` and CI run them. See [docs/devops.md](../docs/devops.md#plugging-in-the-stack).

## Call lock order

Any transaction that touches a call's `pings`, `call_secrets`, `outbox` or `call_events` calls `app.calls_lock.lock_call(conn, call_id)` first, so the `calls` row is always locked before the others. `app.events.append_event` re-locks the same row (its `last_seq` bump), which adds no second lock. Write `call_secrets` only through `write_call_secrets`, and end a call only through `end_call_transition`. `test_end_vs_reply_no_deadlock` guards this order.
