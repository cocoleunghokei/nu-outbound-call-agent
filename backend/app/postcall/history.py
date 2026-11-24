"""Read API for the post-call screens: call history (PO-6) and one call's result (PO-1 to PO-4).

- ``GET /calls``: the caller's calls, newest first.
- ``GET /calls/{call_id}/result``: outcome, what was agreed (only the details the business
  said, see ``shown_details``), open items, decisions, what was shared and the final
  transcript.

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
from app.contracts.generated import OutcomeDetails, ReportOutcomeArgs
from app.db import get_pool
from app.errors import ApiError
from app.postcall.decide import call_days_for, confirmed_details
from app.postcall.jobs import latest_report

router = APIRouter()

MAX_CALLS = 100

# Outcomes for which the agent's report (business_confirmed, details) was checked and kept.
ACCEPTED_CLAIMS = frozenset({"booked", "cancelled", "rescheduled"})


def claims_accepted(report: ReportOutcomeArgs | None, outcome: str | None) -> bool:
    """True if the report's claims (business_confirmed, details) may be shown: the outcome
    job accepted a booked, cancelled or rescheduled report, and it is this same report. A
    report stored after the job ran, with another outcome, was never checked."""
    return report is not None and outcome in ACCEPTED_CLAIMS and report.outcome.value == outcome


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
                " left join public.briefs b on b.id = c.brief_id and b.user_id = c.user_id"
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
                " left join public.briefs b on b.id = c.brief_id and b.user_id = c.user_id"
                " where c.id = %s and c.user_id = %s",
                (call_id, user_id),
            )
        ).fetchone()
        if row is None:
            raise ApiError("not_found", 404, "No such call", False)
        call = _history_item(row)
        report = await latest_report(conn, call_id)
        # The agent's claims only stand when the outcome job accepted them. A report that
        # was downgraded to needs_follow_up must not show as a confirmed booking.
        accepted = claims_accepted(report, call.outcome)
        transcript = await _transcript(conn, call_id)
        return CallResultResponse(
            call=call,
            business_confirmed=report.business_confirmed if report and accepted else False,
            details=shown_details(report, accepted, call, transcript),
            open_items=list(report.open_items or []) if report else [],
            decisions=await _decisions(conn, call_id),
            shared_fields=await _shared_fields(conn, call_id),
            transcript=transcript,
        )


def shown_details(
    report: ReportOutcomeArgs | None,
    accepted: bool,
    call: HistoryItem,
    transcript: list[TranscriptLineOut],
) -> OutcomeDetails:
    """The agreed details the result page may show (PO-2): none unless the outcome job
    accepted the report, and then only those the business said in the transcript shown on
    the same page (``confirmed_details``). A detail the agent made up is left out."""
    if report is None or not accepted:
        return OutcomeDetails()
    business = [line.text for line in transcript if line.role == "business"]
    call_days = call_days_for(call.created_at, call.ended_at or call.created_at)
    turns = [(line.role, line.text) for line in transcript]
    return confirmed_details(report.details, business, call_days=call_days, turns=turns)


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
            # seq is arrival order; display order is when the line was spoken
            # (backend/app/events.py: occurred_at is the source start time when known).
            " where call_id = %s and type = 'transcript.final' order by occurred_at, seq",
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
        lines.append(
            TranscriptLineOut(
                seq=seq,
                role=role,
                text=text,
                started_at=parse_started(payload.get("source_start_ts")),
            )
        )
    return lines


def parse_started(value: object) -> datetime | None:
    """A line's start time, or None if it is missing or unreadable. One bad row keeps its
    text (the transcript stays complete) instead of failing the whole result with a 500."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
