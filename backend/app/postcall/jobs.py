"""The ``jobs`` outbox lane: post-call processing after ``end-of-call-report``.

Two independent jobs per call, each its own outbox row, so one failing never blocks or
overwrites the other (docs/architecture.md, post-call processing):

- ``outcome`` (PO-1): decide the outcome (``decide.decide_outcome``), write
  ``calls.outcome`` once, then append ``call.ended`` with that outcome. ``call.ended`` goes
  out only after the outcome is known, and only once: the web app keeps the first one.
- ``shared_fields`` (PO-3): mark each profile field the agent said out loud as ``spoken``
  and append ``field.shared``.

A job that raises is retried with backoff (2, 4, 8, ... up to 60 s) and marked ``failed``
after ``MAX_ATTEMPTS``. Every write is guarded or deduplicated, so a rerun changes nothing.
The summary job (PO-2, LLM) and the call-ended push (PO-5) are not here yet.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from app.calls_lock import CallNotFound, lock_call
from app.contracts.generated import ReportOutcomeArgs
from app.events import append_event
from app.postcall.decide import PAUSE_CAP, decide_outcome, spoken_fields
from app.postcall.handlers import JOB_OUTCOME, JOB_SHARED_FIELDS
from app.registry import AppContext

logger = logging.getLogger(__name__)

POLL_EVERY_S = 2.0
MAX_ATTEMPTS = 5
MAX_JOBS_PER_PASS = 20
CALL_ENDED_DEDUP = "call.ended"


async def postcall_worker(ctx: AppContext) -> None:
    """Worker: run due post-call jobs every 2 s."""
    if ctx.pool is None:
        return
    while True:
        try:
            await run_due_jobs(ctx.pool)
        except Exception:
            logger.exception("postcall: jobs pass failed")
        await asyncio.sleep(POLL_EVERY_S)


async def run_due_jobs(pool: AsyncConnectionPool[Any]) -> int:
    """One pass over due ``jobs`` rows. Returns how many jobs finished."""
    async with pool.connection() as conn:
        due = await (
            await conn.execute(
                "select id, call_id from public.outbox"
                " where lane = 'jobs' and state = 'pending' and not_before <= now()"
                " order by id limit %s",
                (MAX_JOBS_PER_PASS,),
            )
        ).fetchall()
    finished = 0
    for row_id, call_id in due:
        try:
            async with pool.connection() as conn, conn.transaction():
                if await _run_job(conn, row_id, call_id):
                    finished += 1
        except CallNotFound:
            continue  # the call was deleted, and its outbox rows with it
        except Exception:
            logger.exception("postcall: job %s for call %s failed; will retry", row_id, call_id)
            await _retry_later(pool, row_id)
    return finished


async def _run_job(conn: AsyncConnection[Any], row_id: int, call_id: UUID) -> bool:
    """Under the call lock: claim the row if still pending, run it, mark it sent."""
    await lock_call(conn, call_id)
    row = await (
        await conn.execute(
            "select kind, payload from public.outbox"
            " where id = %s and lane = 'jobs' and state = 'pending' for update",
            (row_id,),
        )
    ).fetchone()
    if row is None:
        return False  # another pass took it
    kind, payload = row
    payload = payload if isinstance(payload, dict) else {}
    if kind == JOB_OUTCOME:
        await run_outcome_job(conn, call_id, payload)
    elif kind == JOB_SHARED_FIELDS:
        await run_shared_fields_job(conn, call_id)
    else:
        logger.warning("postcall: unknown job kind %r on row %s", kind, row_id)
        await conn.execute("update public.outbox set state = 'failed' where id = %s", (row_id,))
        return False
    await conn.execute(
        "update public.outbox set state = 'sent', attempts = attempts + 1 where id = %s",
        (row_id,),
    )
    return True


async def _retry_later(pool: AsyncConnectionPool[Any], row_id: int) -> None:
    """Count the failed attempt and push the row back, or give up after MAX_ATTEMPTS."""
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            "update public.outbox set attempts = attempts + 1,"
            " state = case when attempts + 1 >= %s then 'failed' else state end,"
            " not_before = now() + make_interval(secs => least(60, power(2, attempts + 1)))"
            " where id = %s and state = 'pending'",
            (MAX_ATTEMPTS, row_id),
        )


async def run_outcome_job(
    conn: AsyncConnection[Any], call_id: UUID, payload: dict[str, Any]
) -> None:
    """PO-1: decide and store the outcome, then announce the end with ``call.ended``."""
    raw_reason = payload.get("ended_reason")
    vapi_reason = raw_reason if isinstance(raw_reason, str) else ""
    report = await latest_report(conn, call_id)
    pause_capped = await was_pause_capped(conn, call_id)
    decision = decide_outcome(
        report,
        vapi_reason,
        await transcript_lines(conn, call_id, "business"),
        pause_capped=pause_capped,
    )
    # Guarded: once set, the outcome never changes (one outcome per call).
    await conn.execute(
        "update public.calls set outcome = %s where id = %s and outcome is null",
        (decision.outcome, call_id),
    )
    stored = await (
        await conn.execute("select outcome from public.calls where id = %s", (call_id,))
    ).fetchone()
    outcome = stored[0] if stored is not None else decision.outcome
    ended_reason = PAUSE_CAP if pause_capped and report is None else (vapi_reason or "unknown")
    await append_event(
        conn,
        call_id=call_id,
        type="call.ended",
        source="backend",
        payload={"outcome": outcome, "ended_reason": ended_reason},
        occurred_at=datetime.now(UTC),
        dedup_key=CALL_ENDED_DEDUP,
    )
    logger.info("postcall: call %s outcome %s (%s)", call_id, outcome, decision.reason)


async def run_shared_fields_job(conn: AsyncConnection[Any], call_id: UUID) -> None:
    """PO-3: mark the profile fields the agent said out loud as ``spoken``."""
    snapshot = {
        key: value
        for key, value in await (
            await conn.execute(
                "select key, value from public.call_profile_snapshots where call_id = %s",
                (call_id,),
            )
        ).fetchall()
    }
    for key in spoken_fields(snapshot, await transcript_lines(conn, call_id, "agent")):
        await conn.execute(
            "insert into public.shared_fields (call_id, field_key, state, spoken_at)"
            " values (%s, %s, 'spoken', now())"
            " on conflict (call_id, field_key) do update"
            " set state = 'spoken', spoken_at = coalesce(public.shared_fields.spoken_at, now())"
            " where public.shared_fields.state <> 'spoken'",
            (call_id, key),
        )
        await append_event(
            conn,
            call_id=call_id,
            type="field.shared",
            source="backend",
            payload={"field_key": key, "state": "spoken"},
            occurred_at=datetime.now(UTC),
            dedup_key=f"field:{key}:spoken",
        )


async def latest_report(conn: AsyncConnection[Any], call_id: UUID) -> ReportOutcomeArgs | None:
    """The agent's most recent handled ``report_outcome``, or None if there is none."""
    row = await (
        await conn.execute(
            "select arguments from public.tool_calls"
            " where call_id = %s and name = 'report_outcome' and state = 'handled'"
            " order by source_ts desc nulls last, created_at desc limit 1",
            (call_id,),
        )
    ).fetchone()
    if row is None or not isinstance(row[0], dict):
        return None
    try:
        return ReportOutcomeArgs.model_validate(row[0])
    except ValidationError:
        logger.warning("postcall: call %s has an unreadable report_outcome", call_id)
        return None


async def transcript_lines(conn: AsyncConnection[Any], call_id: UUID, role: str) -> list[str]:
    """The text of every final transcript line spoken by ``role``, in call order."""
    rows = await (
        await conn.execute(
            "select payload from public.call_events"
            " where call_id = %s and type = 'transcript.final' order by seq",
            (call_id,),
        )
    ).fetchall()
    lines = []
    for (payload,) in rows:
        if isinstance(payload, dict) and payload.get("role") == role:
            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                lines.append(text)
    return lines


async def was_pause_capped(conn: AsyncConnection[Any], call_id: UUID) -> bool:
    """True if the sweeper ended the call because it was paused too long (DC-11)."""
    row = await (
        await conn.execute(
            "select 1 from public.call_events"
            " where call_id = %s and type = 'agent.decision' and dedup_key = %s",
            (call_id, PAUSE_CAP),
        )
    ).fetchone()
    return row is not None
