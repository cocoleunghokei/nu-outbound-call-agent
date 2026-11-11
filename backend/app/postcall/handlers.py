"""Vapi handlers for the end of a call: ``report_outcome`` (tool) and ``end-of-call-report``.

Both run inside the ingress transaction with the call lock held, so they must not commit
and must not call the network. The slow part, deciding the outcome and checking what was
said, is queued on the outbox ``jobs`` lane and done by the worker in ``jobs.py``.
"""

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.calls_lock import end_call_transition
from app.webhooks.bind import BoundCall, message_of
from app.webhooks.registry import ToolCallContext

NOTED = "Noted. You can say goodbye and end the call."

JOB_OUTCOME = "outcome"
JOB_SHARED_FIELDS = "shared_fields"
POSTCALL_JOBS = (JOB_OUTCOME, JOB_SHARED_FIELDS)


async def handle_report_outcome(conn: AsyncConnection[Any], ctx: ToolCallContext) -> str:
    """Acknowledge the agent's outcome report.

    The validated arguments are already stored on the ``tool_calls`` receipt; the outcome
    job reads them after the call, checks them against the transcript and decides.
    """
    return NOTED


async def handle_end_of_call_report(
    conn: AsyncConnection[Any], call: BoundCall, body: dict[str, Any]
) -> None:
    """End the call (if ``status-update: ended`` hasn't already) and queue the post-call jobs.

    Runs again on every redelivery: the end transition is a no-op once the call has ended,
    and each job is queued at most once per call.
    """
    reason = message_of(body).get("endedReason")
    ended_reason = reason.strip() if isinstance(reason, str) else ""
    await end_call_transition(conn, call.call_id, reason="end-of-call-report", source="vapi")
    for kind in POSTCALL_JOBS:
        await queue_job(conn, call.call_id, kind, {"ended_reason": ended_reason})


async def queue_job(
    conn: AsyncConnection[Any], call_id: UUID, kind: str, payload: dict[str, Any]
) -> None:
    """Insert a ``jobs`` outbox row unless the call already has one of this kind.

    The caller holds the call lock (``lock_call``), so two deliveries can't both insert.
    """
    await conn.execute(
        "insert into public.outbox (call_id, lane, kind, payload)"
        " select %s, 'jobs', %s, %s"
        " where not exists (select 1 from public.outbox"
        "  where call_id = %s and lane = 'jobs' and kind = %s)",
        (call_id, kind, Jsonb(payload), call_id, kind),
    )
