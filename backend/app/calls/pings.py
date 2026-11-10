"""Ping state changes (DC-7, DC-8, DC-9): open, supersede, answer, expire.

A ping moves only open -> answered | expired | superseded | cancelled
(docs/system-overview.md section 6). A call has at most one open ping; a partial unique index
enforces it. Every function here runs in the caller's transaction with the call lock held.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.calls.store import cancel_fillers, set_status
from app.events import append_event

# The reply window (DC-7, to be tuned from test calls).
PING_WINDOW_MS = 45_000
# "Still checking" lines while a ping is open (DC-7): at 15 s and 30 s of a 45 s window.
FILLER_EVERY_S = 15

PING_COLUMNS = (
    "ping_id, call_id, kind, field_key, question, reason, suggested_replies,"
    " expires_at, remaining_ms, state, vapi_tool_call_id, source_ts"
)


@dataclass(frozen=True)
class PingRow:
    """A row of public.pings."""

    ping_id: UUID
    call_id: UUID
    kind: str
    field_key: str | None
    question: str
    reason: str
    suggested_replies: list[str]
    expires_at: datetime | None
    remaining_ms: int | None
    state: str
    vapi_tool_call_id: str
    source_ts: datetime

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> "PingRow":
        """Build from a ``select PING_COLUMNS`` row."""
        replies = row[6] if isinstance(row[6], list) else []
        return cls(
            ping_id=row[0],
            call_id=row[1],
            kind=row[2],
            field_key=row[3],
            question=row[4],
            reason=row[5] or "",
            suggested_replies=[str(r) for r in replies],
            expires_at=row[7],
            remaining_ms=row[8],
            state=row[9],
            vapi_tool_call_id=row[10],
            source_ts=row[11],
        )

    def payload(self) -> dict[str, Any]:
        """The browser-visible ping (contracts/ping.v1.json). No score, no profile values."""
        data: dict[str, Any] = {
            "ping_id": str(self.ping_id),
            "call_id": str(self.call_id),
            "kind": self.kind,
            "question": self.question,
            "reason": self.reason,
            "suggested_replies": list(self.suggested_replies),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "remaining_ms": self.remaining_ms,
            "state": self.state,
        }
        if self.field_key is not None:
            data["field_key"] = self.field_key
        return data


async def open_ping_for_update(conn: AsyncConnection[Any], call_id: UUID) -> PingRow | None:
    """The call's open ping, row-locked, or None."""
    row = await (
        await conn.execute(
            f"select {PING_COLUMNS} from public.pings"
            " where call_id = %s and state = 'open' for update",
            (call_id,),
        )
    ).fetchone()
    return PingRow.from_row(row) if row else None


async def ping_for_update(
    conn: AsyncConnection[Any], call_id: UUID, ping_id: UUID
) -> PingRow | None:
    """One ping of the call, row-locked, or None."""
    row = await (
        await conn.execute(
            f"select {PING_COLUMNS} from public.pings"
            " where ping_id = %s and call_id = %s for update",
            (ping_id, call_id),
        )
    ).fetchone()
    return PingRow.from_row(row) if row else None


async def is_newest_question(
    conn: AsyncConnection[Any], call_id: UUID, source_ts: datetime, tool_call_id: str
) -> bool:
    """True if this question is newer than every question already asked on the call.

    "Newer" means a later source timestamp (Vapi's), not a later arrival, with ties broken by
    tool call id so every replay orders them the same (architecture §4.2).
    """
    row = await (
        await conn.execute(
            "select source_ts, vapi_tool_call_id from public.pings where call_id = %s"
            " order by source_ts desc, vapi_tool_call_id desc limit 1",
            (call_id,),
        )
    ).fetchone()
    if row is None:
        return True
    latest_ts, latest_id = row
    return (source_ts, tool_call_id) > (latest_ts, latest_id)


async def insert_ping(
    conn: AsyncConnection[Any],
    *,
    call_id: UUID,
    kind: str,
    field_key: str | None,
    question: str,
    reason: str,
    suggested_replies: list[str],
    state: str,
    frozen: bool,
    tool_call_id: str,
    source_ts: datetime,
) -> PingRow:
    """Insert a ping. An open one gets a fresh 45 s clock, frozen if the call is paused."""
    row = await (
        await conn.execute(
            "insert into public.pings (call_id, kind, field_key, question, reason,"
            " suggested_replies, state, expires_at, remaining_ms, vapi_tool_call_id, source_ts)"
            " values (%s, %s, %s, %s, %s, %s, %s,"
            " case when %s then clock_timestamp() + make_interval(secs => %s / 1000.0) end,"
            " %s, %s, %s)"
            f" returning {PING_COLUMNS}",
            (
                call_id,
                kind,
                field_key,
                question,
                reason,
                Jsonb(list(suggested_replies)),
                state,
                state == "open" and not frozen,
                PING_WINDOW_MS,
                PING_WINDOW_MS if state == "open" else None,
                tool_call_id,
                source_ts,
            ),
        )
    ).fetchone()
    assert row is not None
    await conn.execute(
        "update public.calls set latest_ask_at = greatest(coalesce(latest_ask_at, %s), %s)"
        " where id = %s",
        (source_ts, source_ts, call_id),
    )
    return PingRow.from_row(row)


async def announce_open_ping(conn: AsyncConnection[Any], ping: PingRow) -> None:
    """Tell the browser about a newly opened ping (``ping.opened``)."""
    await append_event(
        conn,
        call_id=ping.call_id,
        type="ping.opened",
        source="backend",
        payload=ping.payload(),
        occurred_at=datetime.now(UTC),
        dedup_key=f"ping:{ping.ping_id}:opened",
    )


async def close_ping(
    conn: AsyncConnection[Any],
    ping: PingRow,
    state: str,
    *,
    reply_text: str | None = None,
) -> None:
    """Close an open ping: set its state, tell the browser, drop its fillers, resume Talking.

    The call goes back from Waiting on you to Talking unless another ping is opening in the
    same transaction (supersede) or the call is paused, which keeps it On hold.
    """
    await conn.execute(
        "update public.pings set state = %s,"
        " answered_at = case when %s = 'answered' then clock_timestamp() else answered_at end"
        " where ping_id = %s and state = 'open'",
        (state, state, ping.ping_id),
    )
    payload: dict[str, Any] = {"ping_id": str(ping.ping_id), "state": state}
    if reply_text is not None:
        payload["reply_text"] = reply_text
    await append_event(
        conn,
        call_id=ping.call_id,
        type="ping.resolved",
        source="backend",
        payload=payload,
        occurred_at=datetime.now(UTC),
        dedup_key=f"ping:{ping.ping_id}:resolved",
    )
    await cancel_fillers(conn, ping.call_id, ping.ping_id)
    if state != "superseded":
        await set_status(conn, ping.call_id, "talking", only_from=frozenset({"waiting_on_you"}))
