"""Database helpers shared by the ask manager, the reply route, the sweeper and the outbox worker.

Every function runs inside the caller's transaction, and every caller holds the call lock
(``calls_lock.lock_call``) first, as the lock order in backend/README.md requires.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.calls.messages import DEFAULT_CALLING_NAME
from app.events import append_event

# The kinds of control-lane outbox rows this package writes.
CONTROL_REPLY = "reply"
CONTROL_TIMEOUT = "timeout"
CONTROL_FILLER = "filler"
CONTROL_HOLD = "hold"  # the hold line when the user pauses
CONTROL_MUTE = "mute"
CONTROL_UNMUTE = "unmute"
CONTROL_REASSURE = "reassure"  # every 15 s while paused
CONTROL_GOODBYE = "goodbye"  # End or the pause cap: once queued, the call is ending
CONTROL_HANGUP = "hangup"  # end-call, 6 s after the goodbye if the call is still up

# Rows that may still go out once the call is ending.
ENDING_KINDS = frozenset({CONTROL_GOODBYE, CONTROL_HANGUP})
# Rows that only make sense while the call is paused.
PAUSE_KINDS = frozenset({CONTROL_HOLD, CONTROL_MUTE, CONTROL_REASSURE})
# Answers for the agent wait while the user has the call paused, and go out after Resume.
HELD_WHILE_PAUSED = frozenset({CONTROL_REPLY, CONTROL_TIMEOUT})


@dataclass(frozen=True)
class SnapshotField:
    """One row of the per-call profile snapshot. ``value`` is private: never log it."""

    key: str
    value: str
    sharing: str


async def calling_name(conn: AsyncConnection[Any], call_id: UUID) -> str:
    """The name the agent uses for the user (always shared, DC-1), or a neutral fallback."""
    field = await snapshot_field(conn, call_id, "calling_name")
    if field is None or not field.value.strip():
        return DEFAULT_CALLING_NAME
    return field.value.strip()


async def snapshot_field(
    conn: AsyncConnection[Any], call_id: UUID, key: str
) -> SnapshotField | None:
    """Read one field of the call's profile snapshot, or None."""
    row = await (
        await conn.execute(
            "select key, value, sharing from public.call_profile_snapshots"
            " where call_id = %s and key = %s",
            (call_id, key),
        )
    ).fetchone()
    if row is None:
        return None
    return SnapshotField(key=row[0], value=row[1], sharing=row[2])


async def set_status(
    conn: AsyncConnection[Any], call_id: UUID, to: str, *, only_from: frozenset[str]
) -> bool:
    """Move the call to ``to`` if its status is one of ``only_from``; append ``status.changed``.

    The guarded UPDATE makes it safe to run twice: the second run changes nothing and appends
    nothing. Returns True if the status changed.
    """
    row = await (
        await conn.execute(
            "update public.calls set status = %s where id = %s and status = any(%s)"
            " and status <> %s returning id",
            (to, call_id, list(only_from), to),
        )
    ).fetchone()
    if row is None:
        return False
    await append_event(
        conn,
        call_id=call_id,
        type="status.changed",
        source="backend",
        payload={"status": to},
        occurred_at=datetime.now(UTC),
    )
    return True


async def queue_control(
    conn: AsyncConnection[Any],
    call_id: UUID,
    kind: str,
    payload: dict[str, Any],
    *,
    delay_s: float = 0,
) -> None:
    """Queue a control-URL command for the outbox worker, sent in id order per call (§4.1)."""
    await conn.execute(
        "insert into public.outbox (call_id, lane, kind, payload, not_before)"
        " values (%s, 'control', %s, %s, now() + make_interval(secs => %s))",
        (call_id, kind, Jsonb(payload), delay_s),
    )


async def queue_notify(conn: AsyncConnection[Any], call_id: UUID, ping_id: UUID) -> None:
    """Queue the Web Push for a new ping. Steve's notifier owns the ``notify`` lane."""
    await conn.execute(
        "insert into public.outbox (call_id, lane, kind, payload)"
        " values (%s, 'notify', 'ping', %s)",
        (call_id, Jsonb({"ping_id": str(ping_id)})),
    )


async def is_ending(conn: AsyncConnection[Any], call_id: UUID) -> bool:
    """True once End (or the pause cap) has queued the goodbye: nothing new may start."""
    row = await (
        await conn.execute(
            "select 1 from public.outbox where call_id = %s and lane = 'control'"
            " and kind = %s and state <> 'cancelled' limit 1",
            (call_id, CONTROL_GOODBYE),
        )
    ).fetchone()
    return row is not None


async def cancel_pending_controls(
    conn: AsyncConnection[Any], call_id: UUID, kinds: frozenset[str] | None = None
) -> None:
    """Cancel the call's pending control rows, or only those of ``kinds``."""
    if kinds is None:
        await conn.execute(
            "update public.outbox set state = 'cancelled'"
            " where call_id = %s and lane = 'control' and state = 'pending'",
            (call_id,),
        )
        return
    await conn.execute(
        "update public.outbox set state = 'cancelled'"
        " where call_id = %s and lane = 'control' and state = 'pending' and kind = any(%s)",
        (call_id, list(kinds)),
    )


async def requeue_held_answers(conn: AsyncConnection[Any], call_id: UUID) -> None:
    """Move answers held during a pause behind the rows queued so far (e.g. the unmute).

    Copies keep their kind and payload; the originals are cancelled.
    """
    await conn.execute(
        "with held as ("
        "  update public.outbox set state = 'cancelled'"
        "  where call_id = %s and lane = 'control' and state = 'pending' and kind = any(%s)"
        "  returning call_id, kind, payload, id"
        ") insert into public.outbox (call_id, lane, kind, payload)"
        " select call_id, 'control', kind, payload from held order by id",
        (call_id, list(HELD_WHILE_PAUSED)),
    )


async def cancel_fillers(conn: AsyncConnection[Any], call_id: UUID, ping_id: UUID) -> None:
    """Cancel the "still checking" lines still queued for a ping that has closed."""
    await conn.execute(
        "update public.outbox set state = 'cancelled'"
        " where call_id = %s and lane = 'control' and kind = %s and state = 'pending'"
        " and payload->>'ping_id' = %s",
        (call_id, CONTROL_FILLER, str(ping_id)),
    )
