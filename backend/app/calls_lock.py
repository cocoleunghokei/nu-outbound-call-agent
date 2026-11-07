"""The call lock and the helpers that must hold it.

Lock order (binds every owner): any transaction that touches a call's pings, call_secrets,
outbox or call_events calls ``lock_call(conn, call_id)`` first, so the ``calls`` row is always
locked before any of those rows. ``events.append_event`` re-locks the same row, which is free.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from app.db_tx import require_transaction
from app.events import CallNotFound, EventSource, append_event
from app.secrets_registry import get_secret_registry

__all__ = ["CallNotFound", "CallRow", "end_call_transition", "lock_call", "write_call_secrets"]

logger = logging.getLogger(__name__)

# Statuses with no way out (docs/architecture.md section 4.2).
TERMINAL_STATUSES = frozenset({"ended", "failed"})


@dataclass(frozen=True)
class CallRow:
    """The columns of public.calls that lock holders need."""

    id: UUID
    user_id: UUID
    status: str
    ended_at: datetime | None
    vapi_call_id: str | None
    last_seq: int


async def lock_call(conn: AsyncConnection[Any], call_id: UUID) -> CallRow:
    """Row-lock the call (``select ... for update``) until the caller's transaction ends.

    Raises CallNotFound (a LookupError) if the call does not exist, and RuntimeError if
    ``conn`` is in autocommit mode with no transaction open.
    """
    require_transaction(conn, "lock_call")
    row = await (
        await conn.execute(
            "select id, user_id, status, ended_at, vapi_call_id, last_seq"
            " from public.calls where id = %s for update",
            (call_id,),
        )
    ).fetchone()
    if row is None:
        raise CallNotFound(f"call {call_id} not found")
    return CallRow(
        id=row[0],
        user_id=row[1],
        status=row[2],
        ended_at=row[3],
        vapi_call_id=row[4],
        last_seq=row[5],
    )


async def write_call_secrets(
    conn: AsyncConnection[Any], call_id: UUID, control_url: str, listen_url: str
) -> bool:
    """Store the call's Vapi control and listen URLs, unless the call has already ended.

    Takes the call lock, so it cannot interleave with end_call_transition. Returns True if it
    wrote (insert or update) and False if the call had ended. On a write the URLs are added to
    the process-wide secret registry so they are redacted from logs.
    """
    call = await lock_call(conn, call_id)
    if call.ended_at is not None:
        return False
    await conn.execute(
        "insert into public.call_secrets (call_id, control_url, listen_url) values (%s, %s, %s)"
        " on conflict (call_id) do update set control_url = excluded.control_url,"
        " listen_url = excluded.listen_url, updated_at = now()",
        (call_id, control_url, listen_url),
    )
    registry = get_secret_registry()
    registry.add(control_url)
    registry.add(listen_url)
    return True


async def end_call_transition(
    conn: AsyncConnection[Any], call_id: UUID, *, reason: str, source: EventSource
) -> bool:
    """The one shared end transition, run in the caller's transaction.

    Under the call lock: if the call is already terminal (``ended_at`` set, or status
    ``ended`` or ``failed``; see docs/architecture.md section 4.2), changes nothing and returns
    False. Otherwise sets ``status='ended'`` and ``ended_at=now()``, deletes call_secrets,
    cancels the open ping and the pending ``control`` outbox rows, appends
    ``status.changed {status: "ended"}`` and returns True. ``reason`` (for example the Vapi
    message type) is only logged.
    """
    call = await lock_call(conn, call_id)
    if call.ended_at is not None or call.status in TERMINAL_STATUSES:
        return False
    await conn.execute(
        "update public.calls set status = 'ended', ended_at = now() where id = %s", (call_id,)
    )
    await conn.execute("delete from public.call_secrets where call_id = %s", (call_id,))
    await conn.execute(
        "update public.pings set state = 'cancelled' where call_id = %s and state = 'open'",
        (call_id,),
    )
    await conn.execute(
        "update public.outbox set state = 'cancelled'"
        " where call_id = %s and lane = 'control' and state = 'pending'",
        (call_id,),
    )
    await append_event(
        conn,
        call_id=call_id,
        type="status.changed",
        source=source,
        payload={"status": "ended"},
        occurred_at=datetime.now(UTC),
        dedup_key=None,
    )
    logger.info("call %s ended (%s, source=%s)", call_id, reason, source)
    return True
