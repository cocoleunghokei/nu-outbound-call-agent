"""``POST /calls/{call_id}/pings/{ping_id}/reply``: the user's answer to a ping (DC-6, DC-8).

The reply and the 45 s timeout race. Exactly one wins, decided under the ping's row lock by
comparing ``expires_at`` with ``clock_timestamp()`` (the time after the lock, not the
transaction start). A reply that loses gets ``409 ping_closed`` (architecture §4.2).
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.auth import current_user
from app.calls import messages
from app.calls.pings import PingRow, close_ping, ping_for_update
from app.calls.store import CONTROL_REPLY, calling_name, queue_control, snapshot_field
from app.calls_lock import CallNotFound, lock_call
from app.contracts.generated import PingReplyChoice, PingReplyCommand, PingReplyText
from app.db import get_pool
from app.errors import ApiError
from app.events import append_event
from app.idempotency import claim_idempotency, idempotency_key, store_idempotent_response

router = APIRouter()


def _not_found() -> ApiError:
    return ApiError("not_found", 404, "No such ping on this call", False)


def _ping_closed() -> ApiError:
    return ApiError(
        "ping_closed", 409, "This question already closed: it timed out or was replaced", False
    )


def _invalid_reply(message: str) -> ApiError:
    return ApiError("invalid_reply", 422, message, False)


async def _ping_is_open(conn: AsyncConnection[Any], ping: PingRow) -> bool:
    """True while the ping can still take a reply: open, and paused or not yet expired."""
    if ping.state != "open":
        return False
    if ping.expires_at is None:  # paused: the clock is frozen, a reply is allowed
        return True
    row = await (await conn.execute("select clock_timestamp() < %s", (ping.expires_at,))).fetchone()
    return bool(row and row[0])


async def _apply_reply(
    conn: AsyncConnection[Any], ping: PingRow, reply: PingReplyChoice | PingReplyText
) -> str:
    """Close the ping as answered and queue the answer for the agent. Returns the reply text."""
    name = await calling_name(conn, ping.call_id)

    if ping.kind == "field_approval":
        # Only an explicit Share releases the field, for this call only (DC-6).
        if not isinstance(reply, PingReplyChoice):
            raise _invalid_reply("Choose Share it or Don't share")
        if reply.choice >= len(messages.FIELD_APPROVAL_REPLIES) or ping.field_key is None:
            raise _invalid_reply("That choice is not one of the replies")
        label = messages.field_label(ping.field_key)
        reply_text = messages.FIELD_APPROVAL_REPLIES[reply.choice]
        field = await snapshot_field(conn, ping.call_id, ping.field_key)
        if reply.choice == messages.SHARE_CHOICE and field is not None:
            content = messages.field_shared_message(name, ping.question, label, field.value)
            await _record_shared_field(conn, ping)
        else:
            content = messages.field_declined_message(name, ping.question, label)
    else:
        if isinstance(reply, PingReplyChoice):
            if reply.choice >= len(ping.suggested_replies):
                raise _invalid_reply("That choice is not one of the replies")
            reply_text = ping.suggested_replies[reply.choice]
        else:
            reply_text = messages.sanitize_reply_text(reply.text)
            if not reply_text:
                raise _invalid_reply("The reply is empty")
        content = messages.answer_message(name, ping.question, reply_text)

    await close_ping(conn, ping, "answered", reply_text=reply_text)
    await queue_control(
        conn,
        ping.call_id,
        CONTROL_REPLY,
        {"ping_id": str(ping.ping_id), "message": messages.add_message(content)},
    )
    return reply_text


async def _record_shared_field(conn: AsyncConnection[Any], ping: PingRow) -> None:
    """Log the approval for the post-call "what was shared" list (PO-3). No value is stored."""
    assert ping.field_key is not None
    await conn.execute(
        "insert into public.shared_fields (call_id, field_key, state, approved_at)"
        " values (%s, %s, 'approved', now()) on conflict (call_id, field_key) do nothing",
        (ping.call_id, ping.field_key),
    )
    await append_event(
        conn,
        call_id=ping.call_id,
        type="field.shared",
        source="user",
        payload={"field_key": ping.field_key, "state": "approved"},
        occurred_at=datetime.now(UTC),
        dedup_key=f"field:{ping.field_key}:approved",
    )


@router.post("/calls/{call_id}/pings/{ping_id}/reply")
async def reply_to_ping(
    call_id: uuid.UUID,
    ping_id: uuid.UUID,
    body: PingReplyCommand,
    user_id: Annotated[uuid.UUID, Depends(current_user)],
    key: Annotated[uuid.UUID, Depends(idempotency_key)],
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
) -> dict[str, str]:
    """Answer an open ping with a suggested reply (by index) or free text.

    Returns ``{"ping_id", "state": "answered", "reply_text"}``. ``200`` means the answer won
    the race and is queued for the agent, not that the agent has spoken it yet.
    """
    async with pool.connection() as conn, conn.transaction():
        # An error rolls this transaction back, claim included, so only a winning reply is
        # stored; a retried key after a 409 is simply evaluated again (and loses again).
        stored = await claim_idempotency(conn, user_id, key)
        if stored is not None:
            return dict(stored.body)
        try:
            call = await lock_call(conn, call_id)
        except CallNotFound:
            raise _not_found() from None
        if call.user_id != user_id:
            raise _not_found()
        ping = await ping_for_update(conn, call_id, ping_id)
        if ping is None:
            raise _not_found()
        if not await _ping_is_open(conn, ping):
            raise _ping_closed()
        reply_text = await _apply_reply(conn, ping, body.root)
        response = {"ping_id": str(ping_id), "state": "answered", "reply_text": reply_text}
        await store_idempotent_response(conn, user_id, key, 200, response)
        return response
