"""The per-call event log. append_event is the only way to write to public.call_events.

``seq`` is arrival order, gap-free per call, for replay and gap detection. Display order for
transcripts is ``payload.source_start_ts``, falling back to ``occurred_at`` (received time)
when Vapi does not send one.
"""

import re
from datetime import datetime
from typing import Any, Literal, get_args
from uuid import UUID

from psycopg import AsyncConnection, Rollback
from psycopg.types.json import Jsonb

from app.contracts import validator_for
from app.contracts.generated import CallEvent
from app.db_tx import require_transaction

EventSource = Literal["vapi", "backend", "user"]

# Event types look like "status.changed" or "transcript.final"; anything else can never name
# a schema file and must not be joined into a path.
_EVENT_TYPE = re.compile(r"^[a-z][a-z_]*(\.[a-z][a-z_]*)+$")


class ContractError(ValueError):
    """An event does not match its contract (unknown type, bad payload, bad envelope field)."""


class CallNotFound(LookupError):
    """No public.calls row has the given id."""


def validate_event(type: str, source: str, payload: dict[str, Any], occurred_at: datetime) -> None:
    """Raise ContractError unless the event matches its writer (closed-enum) contract."""
    if source not in get_args(EventSource):
        raise ContractError(f"unknown event source {source!r}")
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise ContractError("occurred_at must be timezone-aware")
    if not _EVENT_TYPE.match(type):
        raise ContractError(f"unknown event type {type!r}")
    try:
        validator = validator_for(f"payloads/{type}.v1.json")
    except FileNotFoundError as exc:
        raise ContractError(f"unknown event type {type!r}") from exc
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
    if errors:
        details = "; ".join(f"{list(e.path)}: {e.message}" for e in errors)
        raise ContractError(f"invalid {type} payload: {details}")


async def append_event(
    conn: AsyncConnection[Any],
    *,
    call_id: UUID,
    type: str,
    source: EventSource,
    payload: dict[str, Any],
    occurred_at: datetime,
    dedup_key: str | None = None,
) -> CallEvent | None:
    """Append one event to the call's log inside the caller's transaction.

    Validates before touching the database (raises ContractError). Then, in a savepoint, bumps
    ``calls.last_seq`` (which row-locks the call and serialises seq) and inserts the event. If
    ``dedup_key`` was already used for this call, the savepoint is rolled back, so no seq is
    burned, and None is returned. ``dedup_key=None`` never deduplicates. Raises CallNotFound if
    the call does not exist, and RuntimeError if ``conn`` is in autocommit mode with no
    transaction open (the event must commit with the caller's other work, never alone).

    Lock order: callers touching pings, call_secrets or the outbox take
    ``calls_lock.lock_call`` first; this function then re-locks the same row.
    """
    validate_event(type, source, payload, occurred_at)
    require_transaction(conn, "append_event")
    event: CallEvent | None = None
    async with conn.transaction() as savepoint:
        bumped = await (
            await conn.execute(
                "update public.calls set last_seq = last_seq + 1 where id = %s returning last_seq",
                (call_id,),
            )
        ).fetchone()
        if bumped is None:
            raise CallNotFound(f"call {call_id} not found")
        row = await (
            await conn.execute(
                "insert into public.call_events"
                " (call_id, seq, type, source, dedup_key, occurred_at, schema_version, payload)"
                " values (%s, %s, %s, %s, %s, %s, 1, %s)"
                " on conflict (call_id, dedup_key) do nothing"
                " returning event_id, call_id, seq, type, source, dedup_key, occurred_at,"
                " schema_version, payload",
                (call_id, bumped[0], type, source, dedup_key, occurred_at, Jsonb(payload)),
            )
        ).fetchone()
        if row is None:
            raise Rollback(savepoint)
        event = CallEvent(
            event_id=row[0],
            call_id=row[1],
            seq=row[2],
            type=row[3],
            source=row[4],
            dedup_key=row[5],
            occurred_at=row[6],
            schema_version=row[7],
            payload=row[8],
        )
    return event
