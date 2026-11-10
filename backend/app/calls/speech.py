"""The quiet rule for Pause (DC-11, architecture §4.1): mute only once the hold line is done.

Vapi's ``speech-update`` messages don't say which sentence they belong to, so the mute can't
wait for "the hold line finished" directly. Instead: once the hold line has been sent, the
mute goes out after the assistant has been silent for 0.7 s following an assistant
``stopped`` event that arrived after the hold line was sent. If the assistant starts
speaking again first, the mute waits for the next ``stopped``. The 4 s deadline set at Pause
always applies, so the mute is never later than that.

Event handlers run again on every redelivery. Re-running this one only moves the mute
within the same window, never past the deadline.
"""

from typing import Any

from psycopg import AsyncConnection

from app.calls.store import CONTROL_HOLD, CONTROL_MUTE
from app.webhooks.bind import BoundCall, message_of

QUIET_MS = 700


async def handle_speech_update(
    conn: AsyncConnection[Any], call: BoundCall, body: dict[str, Any]
) -> None:
    """Move the pending mute earlier on assistant silence, or back to its deadline on speech."""
    message = message_of(body)
    if message.get("role") != "assistant" or message.get("status") not in ("started", "stopped"):
        return
    hold = await (
        await conn.execute(
            "select o.id, (o.payload->>'sent_at')::timestamptz from public.outbox o"
            " join public.calls c on c.id = o.call_id"
            " where o.call_id = %s and c.status = 'on_hold' and o.lane = 'control'"
            " and o.kind = %s and o.state = 'sent' and o.payload ? 'sent_at'"
            " order by o.id desc limit 1",
            (call.call_id, CONTROL_HOLD),
        )
    ).fetchone()
    if hold is None:
        return  # not paused, or the hold line hasn't gone out yet
    hold_id, hold_sent_at = hold
    if message["status"] == "stopped":
        await conn.execute(
            "update public.outbox set not_before = least("
            " (payload->>'deadline')::timestamptz, now() + make_interval(secs => %s / 1000.0))"
            " where call_id = %s and lane = 'control' and kind = %s and state = 'pending'"
            " and id > %s and now() > %s",
            (QUIET_MS, call.call_id, CONTROL_MUTE, hold_id, hold_sent_at),
        )
    else:
        await conn.execute(
            "update public.outbox set not_before = (payload->>'deadline')::timestamptz"
            " where call_id = %s and lane = 'control' and kind = %s and state = 'pending'"
            " and id > %s",
            (call.call_id, CONTROL_MUTE, hold_id),
        )
