"""``POST /calls/{call_id}/commands``: Pause, Resume and End call (DC-11).

Each command changes the call's state in one transaction under the call lock and queues the
control-URL messages that make it happen on the line. ``200`` means the command was accepted
and queued, not that the business has heard it yet; the screen follows the events.

``instruction`` (DC-10) belongs to the pre-call owner and is answered ``422`` here until that
handler lands.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.auth import current_user
from app.calls import messages
from app.calls.pings import (
    FILLER_EVERY_S,
    close_ping,
    freeze_ping,
    open_ping_for_update,
    restore_ping,
)
from app.calls.store import (
    CONTROL_FILLER,
    CONTROL_GOODBYE,
    CONTROL_HANGUP,
    CONTROL_HOLD,
    CONTROL_MUTE,
    CONTROL_REASSURE,
    CONTROL_UNMUTE,
    PAUSE_KINDS,
    calling_name,
    cancel_pending_controls,
    is_ending,
    queue_control,
    requeue_held_answers,
    set_status,
)
from app.calls_lock import CallNotFound, CallRow, lock_call
from app.contracts.generated import CallCommand, CallCommandType
from app.db import get_pool
from app.errors import ApiError
from app.events import append_event
from app.idempotency import claim_idempotency, idempotency_key, store_idempotent_response

router = APIRouter()

# The pause cap (DC-11, to be tuned from test calls). The sweeper enforces it.
PAUSE_CAP_S = 120
REASSURE_EVERY_S = 15
# Pause: if the hold line hasn't finished by then, mute anyway (architecture §4.1).
MUTE_AFTER_S = 4
# End: hang up if the goodbye didn't end the call within this long.
HANGUP_AFTER_S = 6

TERMINAL_STATUSES = frozenset({"ended", "failed"})
PAUSABLE_STATUSES = frozenset({"talking", "waiting_on_you", "in_phone_menu"})


def _call_state_error(message: str) -> ApiError:
    return ApiError("invalid_call_state", 409, message, False)


async def _command_applied(conn: AsyncConnection[Any], call_id: uuid.UUID, command: str) -> None:
    await append_event(
        conn,
        call_id=call_id,
        type="command.applied",
        source="user",
        payload={"command": command},
        occurred_at=datetime.now(UTC),
    )


async def pause_call(conn: AsyncConnection[Any], call: CallRow) -> None:
    """Hold line, then mute; freeze an open ping's clock; reassure every 15 s up to the cap."""
    if call.status == "on_hold":
        return  # already paused: nothing to do
    if call.status not in PAUSABLE_STATUSES:
        raise _call_state_error("You can pause once they pick up")
    name = await calling_name(conn, call.id)
    await conn.execute(
        "update public.calls set status = 'on_hold', paused_at = now() where id = %s",
        (call.id,),
    )
    await _command_applied(conn, call.id, "pause")
    await append_event(
        conn,
        call_id=call.id,
        type="status.changed",
        source="backend",
        payload={"status": "on_hold"},
        occurred_at=datetime.now(UTC),
    )
    ping = await open_ping_for_update(conn, call.id)
    if ping is not None:
        await freeze_ping(conn, ping)
    await cancel_pending_controls(conn, call.id, frozenset({CONTROL_FILLER}))
    await queue_control(
        conn, call.id, CONTROL_HOLD, {"message": messages.say(messages.hold_line())}
    )
    await queue_control(
        conn, call.id, CONTROL_MUTE, {"message": messages.mute_assistant()}, delay_s=MUTE_AFTER_S
    )
    for delay_s in range(REASSURE_EVERY_S, PAUSE_CAP_S, REASSURE_EVERY_S):
        await queue_control(
            conn,
            call.id,
            CONTROL_REASSURE,
            {
                # Unmute just long enough to speak, then mute again (to confirm in probe 6).
                "messages": [
                    messages.unmute_assistant(),
                    messages.say(messages.reassurance_line(name)),
                    messages.mute_assistant(),
                ]
            },
            delay_s=delay_s,
        )


async def resume_call(conn: AsyncConnection[Any], call: CallRow) -> None:
    """Unmute, restart an open ping's clock where it stopped, then send held answers."""
    if call.status != "on_hold":
        return  # not paused: nothing to do
    name = await calling_name(conn, call.id)
    await conn.execute("update public.calls set paused_at = null where id = %s", (call.id,))
    await _command_applied(conn, call.id, "resume")
    await cancel_pending_controls(conn, call.id, PAUSE_KINDS)
    await queue_control(conn, call.id, CONTROL_UNMUTE, {"message": messages.unmute_assistant()})
    await requeue_held_answers(conn, call.id)
    ping = await open_ping_for_update(conn, call.id)
    if ping is None:
        await set_status(conn, call.id, "talking", only_from=frozenset({"on_hold"}))
        return
    restored = await restore_ping(conn, ping)
    await set_status(conn, call.id, "waiting_on_you", only_from=frozenset({"on_hold"}))
    left_s = (restored.remaining_ms or 0) // 1000
    for delay_s in range(FILLER_EVERY_S, left_s, FILLER_EVERY_S):
        await queue_control(
            conn,
            call.id,
            CONTROL_FILLER,
            {"ping_id": str(ping.ping_id), "message": messages.say(messages.filler_line(name))},
            delay_s=delay_s,
        )


async def begin_ending(conn: AsyncConnection[Any], call: CallRow, goodbye: str) -> None:
    """Say goodbye and hang up. Shared by End call and the pause cap.

    Cancels the open ping and every pending control row, then queues the goodbye (unmuting
    first if paused) with ``endCallAfterSpoken``, and ``end-call`` 6 s later as a fallback.
    Once the goodbye is queued, the outbox sends nothing else for the call.
    The call itself becomes ``ended`` when Vapi reports it (status-update / end-of-call-report).
    """
    ping = await open_ping_for_update(conn, call.id)
    if ping is not None:
        await close_ping(conn, ping, "cancelled")
    await cancel_pending_controls(conn, call.id)
    goodbye_messages = [messages.say_and_hang_up(goodbye)]
    if call.status == "on_hold":
        goodbye_messages.insert(0, messages.unmute_assistant())
    await queue_control(conn, call.id, CONTROL_GOODBYE, {"messages": goodbye_messages})
    await queue_control(
        conn, call.id, CONTROL_HANGUP, {"message": messages.end_call()}, delay_s=HANGUP_AFTER_S
    )
    await conn.execute("update public.calls set paused_at = null where id = %s", (call.id,))


async def end_by_user(conn: AsyncConnection[Any], call: CallRow) -> None:
    """End call: the agent says a polite goodbye and hangs up."""
    name = await calling_name(conn, call.id)
    await _command_applied(conn, call.id, "end")
    await begin_ending(conn, call, messages.goodbye_line(name))


async def apply_command(conn: AsyncConnection[Any], call: CallRow, command: CallCommand) -> None:
    """Apply one command to a locked call. Raises ApiError for a command the call can't take."""
    if command.type == CallCommandType.instruction:
        raise ApiError("unsupported_command", 422, "Instructions are not available yet", False)
    terminal = call.ended_at is not None or call.status in TERMINAL_STATUSES
    ending = terminal or await is_ending(conn, call.id)
    if command.type == CallCommandType.end:
        if not ending:
            await end_by_user(conn, call)
        return  # ending twice is harmless
    if ending:
        raise _call_state_error("The call is ending")
    if command.type == CallCommandType.pause:
        await pause_call(conn, call)
    else:
        await resume_call(conn, call)


@router.post("/calls/{call_id}/commands")
async def send_command(
    call_id: uuid.UUID,
    body: CallCommand,
    user_id: Annotated[uuid.UUID, Depends(current_user)],
    key: Annotated[uuid.UUID, Depends(idempotency_key)],
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
) -> dict[str, str]:
    """Pause, Resume or End the call. Returns ``{"call_id", "command", "status": "accepted"}``.

    Pause and Resume on a call that is already in that state, and End on a call that is
    already ending, change nothing and still answer ``200``.
    """
    async with pool.connection() as conn, conn.transaction():
        stored = await claim_idempotency(conn, user_id, key)
        if stored is not None:
            return dict(stored.body)
        try:
            call = await lock_call(conn, call_id)
        except CallNotFound:
            raise ApiError("not_found", 404, "No such call", False) from None
        if call.user_id != user_id:
            raise ApiError("not_found", 404, "No such call", False)
        await apply_command(conn, call, body)
        response = {"call_id": str(call_id), "command": body.type.value, "status": "accepted"}
        await store_idempotent_response(conn, user_id, key, 200, response)
        return response
