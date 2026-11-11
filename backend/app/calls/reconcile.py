"""Settle calls whose creation never got a clear answer (docs/architecture.md section 4.1).

``POST /calls`` sends exactly one create request and never resends it. Two things can leave a
call without a clear result, and both would otherwise block the brief forever (a call that
is neither ended nor failed counts as "running", so every new Start answers 409):

- **Stuck in ``creating``**: the backend crashed (or the request was cancelled) after the
  attempt was committed but before the result was recorded. After ``STALE_CREATING_S`` with
  no ``vapi_call_id``, the call becomes ``creation_uncertain``.
- **``creation_uncertain``**: the create request timed out or got a 5xx. The sweeper looks
  the call up at Vapi by our ``call_id`` in its metadata:
  - found: adopt it (store the Vapi id and control URL, ``-> dialing``). If it is found
    later than ``CHECKING_S``, the screen has already shown "failed", so the agent
    apologises and hangs up at once;
  - not found by ``max_duration_s + GIVE_UP_AFTER_S``: ``failed``. By then any call Vapi
    did place has hit its own ``maxDurationSeconds``, so starting again can't double-dial.

Nothing here ever sends a second create request.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg_pool import AsyncConnectionPool

from app.calls import messages
from app.calls.commands import begin_ending
from app.calls.create import record_not_placed, record_placed
from app.calls.store import calling_name, set_status
from app.calls_lock import CallNotFound, lock_call
from app.voice.port import CreatedCall, VoiceNotSent, VoicePlatform, VoiceRejected, VoiceUncertain

logger = logging.getLogger(__name__)

# A create request is cut off after 10 s (API_DEADLINE_S), so 15 s means it is no longer in
# flight: the process that sent it is gone.
STALE_CREATING_S = 15
# How long the screen says "checking" before it shows "failed" (section 4.1).
CHECKING_S = 120
# Keep looking for this long past the call's max length before giving up.
GIVE_UP_AFTER_S = 300
# Look for calls created a little before our own attempt, in case the clocks disagree.
LOOKUP_SLACK = timedelta(minutes=1)


@dataclass(frozen=True)
class UncertainCall:
    """A ``creation_uncertain`` call and how long ago its create request was sent."""

    call_id: UUID
    attempted_at: datetime
    age_s: float
    give_up_after_s: float


async def mark_stale_creating(pool: AsyncConnectionPool[Any], limit: int = 50) -> int:
    """Move calls stuck in ``creating`` to ``creation_uncertain``. Returns how many moved.

    Never raises: a failure is logged and retried on the next pass.
    """
    try:
        async with pool.connection() as conn:
            due = await (
                await conn.execute(
                    "select id from public.calls where status = 'creating'"
                    " and vapi_call_id is null and ended_at is null"
                    " and create_attempted_at <= now() - make_interval(secs => %s) limit %s",
                    (STALE_CREATING_S, limit),
                )
            ).fetchall()
    except Exception:
        logger.exception("reconcile could not read stuck creating calls")
        return 0
    moved = 0
    for (call_id,) in due:
        try:
            async with pool.connection() as conn, conn.transaction():
                call = await lock_call(conn, call_id)
                if call.vapi_call_id is not None:
                    continue  # recorded since we looked
                if await set_status(
                    conn, call_id, "creation_uncertain", only_from=frozenset({"creating"})
                ):
                    logger.warning(
                        "call %s: creation result never recorded, now uncertain", call_id
                    )
                    moved += 1
        except CallNotFound:
            continue
        except Exception:
            logger.exception("reconcile failed to mark call %s uncertain", call_id)
    return moved


async def _uncertain_calls(pool: AsyncConnectionPool[Any], limit: int) -> list[UncertainCall]:
    async with pool.connection() as conn:
        found = await (
            await conn.execute(
                "select id, coalesce(create_attempted_at, created_at),"
                " extract(epoch from now() - coalesce(create_attempted_at, created_at)),"
                " max_duration_s + %s"
                " from public.calls where status = 'creation_uncertain' and ended_at is null"
                " order by created_at limit %s",
                (GIVE_UP_AFTER_S, limit),
            )
        ).fetchall()
    return [
        UncertainCall(
            call_id=row[0],
            attempted_at=row[1],
            age_s=float(row[2]),
            give_up_after_s=float(row[3]),
        )
        for row in found
    ]


async def _adopt(pool: AsyncConnectionPool[Any], call: UncertainCall, created: CreatedCall) -> str:
    """Store the call Vapi did place; hang it up at once if the screen already said failed."""
    async with pool.connection() as conn, conn.transaction():
        status = await record_placed(conn, call.call_id, created)
        if call.age_s < CHECKING_S or status != "dialing":
            return status
        locked = await lock_call(conn, call.call_id)
        name = await calling_name(conn, call.call_id)
        await begin_ending(conn, locked, messages.late_call_goodbye_line(name))
        logger.warning("call %s: found %.0f s after creation, ending it", call.call_id, call.age_s)
        return status


async def _give_up(pool: AsyncConnectionPool[Any], call_id: UUID) -> str:
    async with pool.connection() as conn, conn.transaction():
        return await record_not_placed(conn, call_id, uncertain=False)


async def resolve_uncertain(
    pool: AsyncConnectionPool[Any], voice: VoicePlatform, limit: int = 20
) -> int:
    """Look up each ``creation_uncertain`` call at Vapi; adopt it or, past the limit, fail it.

    Returns how many calls it settled. Never raises: a failed lookup is retried next pass.
    """
    try:
        due = await _uncertain_calls(pool, limit)
    except Exception:
        logger.exception("reconcile could not read uncertain calls")
        return 0
    settled = 0
    for call in due:
        try:
            created: CreatedCall | None = None
            try:
                created = await voice.find_call_by_app_id(
                    call.call_id, call.attempted_at.astimezone(UTC) - LOOKUP_SLACK
                )
            except (VoiceUncertain, VoiceNotSent, VoiceRejected) as exc:
                # Can't rule the call out. Past the limit it has ended at Vapi anyway.
                logger.warning("call %s: lookup failed (%s)", call.call_id, type(exc).__name__)
                if call.age_s < call.give_up_after_s:
                    continue
            if created is not None:
                await _adopt(pool, call, created)
                settled += 1
            elif call.age_s >= call.give_up_after_s:
                await _give_up(pool, call.call_id)
                logger.warning("call %s: never found at the voice platform, failed", call.call_id)
                settled += 1
        except CallNotFound:
            continue
        except psycopg.OperationalError as exc:
            logger.warning(
                "reconcile: transient error on call %s (%s)", call.call_id, type(exc).__name__
            )
        except Exception:
            logger.exception("reconcile failed on call %s", call.call_id)
    return settled
