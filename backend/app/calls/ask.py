"""The ``ask_user`` tool handler: the agent pauses and pings the user (DC-6, DC-7).

Runs inside the webhook ingress transaction with the call lock held, so everything it writes
(the ping, its events, the push and the "still checking" lines) commits together or not at
all, and Vapi gets its answer in well under the 500 ms budget: no network calls here.
"""

from datetime import UTC, datetime
from typing import Any

from psycopg import AsyncConnection

from app.calls import messages
from app.calls.pings import (
    FILLER_EVERY_S,
    PING_WINDOW_MS,
    announce_open_ping,
    close_ping,
    insert_ping,
    is_newest_question,
    open_ping_for_update,
)
from app.calls.store import (
    CONTROL_FILLER,
    calling_name,
    is_ending,
    queue_control,
    queue_notify,
    set_status,
    snapshot_field,
)
from app.webhooks.registry import ToolCallContext

TERMINAL_STATUSES = frozenset({"ended", "failed"})
# A new question moves the call to Waiting on you from these. Waiting on you stays as it is
# (a replaced question), and On hold stays On hold (DC-11).
ASKABLE_STATUSES = frozenset({"talking", "in_phone_menu"})


def _text(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    return value.strip() if isinstance(value, str) else ""


def _replies(arguments: dict[str, Any]) -> list[str]:
    value = arguments.get("suggested_replies")
    if not isinstance(value, list):
        return []
    return [r.strip() for r in value if isinstance(r, str) and r.strip()][:4]


async def handle_ask_user(conn: AsyncConnection[Any], ctx: ToolCallContext) -> str:
    """Open a ping for the user and return the hold instruction for the in-call model.

    - An Ask-me-first field (``field_requested``) becomes a ``field_approval`` ping with a
      server-written question and fixed Share / Don't share replies (DC-6). The model's own
      wording is not shown, so it can't leak or misstate the field.
    - A question older than one already asked is stored as ``superseded`` and never shown.
      A newer one replaces the open ping: the user only ever sees one question (DC-7).
    - While the call is paused, the new ping's clock starts frozen (DC-11).
    """
    row = await (
        await conn.execute("select status from public.calls where id = %s", (ctx.call_id,))
    ).fetchone()
    status = row[0] if row else "ended"
    if status in TERMINAL_STATUSES or await is_ending(conn, ctx.call_id):
        return messages.call_over_result()

    name = await calling_name(conn, ctx.call_id)
    source_ts = ctx.source_ts or datetime.now(UTC)

    kind, field_key = "decision", None
    question, reason = _text(ctx.arguments, "question"), _text(ctx.arguments, "reason")
    replies = _replies(ctx.arguments)
    requested = _text(ctx.arguments, "field_requested")
    if requested:
        field = await snapshot_field(conn, ctx.call_id, requested)
        if field is not None and field.sharing == "ask_first":
            kind, field_key = "field_approval", requested
            question, reason = messages.field_approval_question(requested)
            replies = list(messages.FIELD_APPROVAL_REPLIES)
    if not question:
        raise ValueError("ask_user needs a question")

    newest = await is_newest_question(conn, ctx.call_id, source_ts, ctx.tool_call_id)
    paused = status == "on_hold"
    if not newest:
        await insert_ping(
            conn,
            call_id=ctx.call_id,
            kind=kind,
            field_key=field_key,
            question=question,
            reason=reason,
            suggested_replies=replies,
            state="superseded",
            frozen=paused,
            tool_call_id=ctx.tool_call_id,
            source_ts=source_ts,
        )
        return messages.superseded_result()

    previous = await open_ping_for_update(conn, ctx.call_id)
    if previous is not None:
        await close_ping(conn, previous, "superseded")
    ping = await insert_ping(
        conn,
        call_id=ctx.call_id,
        kind=kind,
        field_key=field_key,
        question=question,
        reason=reason,
        suggested_replies=replies,
        state="open",
        frozen=paused,
        tool_call_id=ctx.tool_call_id,
        source_ts=source_ts,
    )
    await announce_open_ping(conn, ping)
    await set_status(conn, ctx.call_id, "waiting_on_you", only_from=ASKABLE_STATUSES)
    await queue_notify(conn, ctx.call_id, ping.ping_id)
    if not paused:
        # Paused calls get the pause reassurance instead (DC-11, B2).
        for delay_s in range(FILLER_EVERY_S, PING_WINDOW_MS // 1000, FILLER_EVERY_S):
            await queue_control(
                conn,
                ctx.call_id,
                CONTROL_FILLER,
                {"ping_id": str(ping.ping_id), "message": messages.say(messages.filler_line(name))},
                delay_s=delay_s,
            )
    return messages.ask_user_result(name)
