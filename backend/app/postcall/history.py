"""Read API for the post-call screens: call history (PO-6) and one call's result (PO-1 to PO-4).

- ``GET /calls``: the caller's calls, newest first.
- ``GET /calls/{call_id}/result``: outcome, what was agreed, open items, decisions, what
  was shared and the final transcript.

These go through the backend, not straight to Supabase, because the agent's outcome report
lives in ``tool_calls``, which the browser can't read. The backend connects as the database
owner, so row-level security does not apply here: every query filters by the caller.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from app.auth import current_user
from app.contracts.generated import OutcomeDetails
from app.db import get_pool
from app.errors import ApiError
from app.postcall.jobs import latest_report

router = APIRouter()

MAX_CALLS = 100


class HistoryItem(BaseModel):
    """One row of the call history (a ``calls`` row with its brief)."""

    id: uuid.UUID
    business_name: str
    phone_e164: str
    goal: str
    template_key: str | None
    status: str
    outcome: str | None
    created_at: datetime
    ended_at: datetime | None


class HistoryResponse(BaseModel):
    calls: list[HistoryItem]


class DecisionOut(BaseModel):
    """An ``agent.decision`` event: what the agent did on its own, and why (DC-5)."""

    situation: str
    action: str
    reason: str
    field_key: str | None = None
    missed_preference: str | None = None


class SharedFieldOut(BaseModel):
    """A profile field disclosed on the call (PO-3)."""

    field_key: str
    state: str


class TranscriptLineOut(BaseModel):
    """A ``transcript.final`` event (PO-4)."""

    seq: int
    role: str
    text: str
    started_at: datetime | None


class CallResultResponse(BaseModel):
    call: HistoryItem
    business_confirmed: bool
    details: OutcomeDetails
    open_items: list[str]
    decisions: list[DecisionOut]
    shared_fields: list[SharedFieldOut]
    transcript: list[TranscriptLineOut]


_CALL_COLUMNS = (
    "c.id, coalesce(b.business_name, ''), coalesce(b.phone_e164, ''), coalesce(b.goal, ''),"
    " b.template_key, c.status, c.outcome, c.created_at, c.ended_at"
)


def _history_item(row: tuple[Any, ...]) -> HistoryItem:
    return HistoryItem(
        id=row[0],
        business_name=row[1],
        phone_e164=row[2],
        goal=row[3],
        template_key=row[4],
        status=row[5],
        outcome=row[6],
        created_at=row[7],
        ended_at=row[8],
    )


@router.get("/calls")
async def list_calls(
    user_id: Annotated[uuid.UUID, Depends(current_user)],
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
) -> HistoryResponse:
    """The caller's calls, newest first (at most 100)."""
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                f"select {_CALL_COLUMNS} from public.calls c"
                " left join public.briefs b on b.id = c.brief_id"
                " where c.user_id = %s order by c.created_at desc limit %s",
                (user_id, MAX_CALLS),
            )
        ).fetchall()
    return HistoryResponse(calls=[_history_item(row) for row in rows])


@router.get("/calls/{call_id}/result")
async def get_call_result(
    call_id: uuid.UUID,
    user_id: Annotated[uuid.UUID, Depends(current_user)],
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
) -> CallResultResponse:
    """One call's result. 404 if the call doesn't exist or isn't the caller's."""
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                f"select {_CALL_COLUMNS} from public.calls c"
                " left join public.briefs b on b.id = c.brief_id"
                " where c.id = %s and c.user_id = %s",
                (call_id, user_id),
            )
        ).fetchone()
        if row is None:
            raise ApiError("not_found", 404, "No such call", False)
        report = await latest_report(conn, call_id)
        return CallResultResponse(
            call=_history_item(row),
            business_confirmed=report.business_confirmed if report else False,
            details=(report.details if report and report.details else OutcomeDetails()),
            open_items=list(report.open_items or []) if report else [],
            decisions=await _decisions(conn, call_id),
            shared_fields=await _shared_fields(conn, call_id),
            transcript=await _transcript(conn, call_id),
        )


async def _decisions(conn: AsyncConnection[Any], call_id: uuid.UUID) -> list[DecisionOut]:
    rows = await (
        await conn.execute(
            "select payload from public.call_events"
            " where call_id = %s and type = 'agent.decision' order by seq",
            (call_id,),
        )
    ).fetchall()
    decisions = []
    for (payload,) in rows:
        if not isinstance(payload, dict):
            continue
        try:
            decisions.append(DecisionOut.model_validate(payload))
        except ValueError:
            continue  # a payload this version can't read is left out, never a 500
    return decisions


async def _shared_fields(conn: AsyncConnection[Any], call_id: uuid.UUID) -> list[SharedFieldOut]:
    rows = await (
        await conn.execute(
            "select field_key, state from public.shared_fields"
            " where call_id = %s order by field_key",
            (call_id,),
        )
    ).fetchall()
    return [SharedFieldOut(field_key=key, state=state) for key, state in rows]


async def _transcript(conn: AsyncConnection[Any], call_id: uuid.UUID) -> list[TranscriptLineOut]:
    rows = await (
        await conn.execute(
            "select seq, payload from public.call_events"
            " where call_id = %s and type = 'transcript.final' order by seq",
            (call_id,),
        )
    ).fetchall()
    lines = []
    for seq, payload in rows:
        if not isinstance(payload, dict):
            continue
        role, text = payload.get("role"), payload.get("text")
        if not isinstance(role, str) or not isinstance(text, str):
            continue
        started = payload.get("source_start_ts")
        lines.append(
            TranscriptLineOut(
                seq=seq,
                role=role,
                text=text,
                started_at=datetime.fromisoformat(started) if isinstance(started, str) else None,
            )
        )
    return lines
