"""The sweeper: closes pings nobody answered in time (DC-9), ends calls paused past the cap
(DC-11), resolves call creations whose outcome is unknown (docs/architecture.md section 4.1,
"Call creation (E6)") and runs pending tool calls.

The timeout loses to a reply that reached the ping's row lock first: both sides re-check the
ping under that lock against ``clock_timestamp()``, so exactly one of them closes it.

Creation recovery never resends a create request. A ``creating`` call that has had a request
in flight for 15 s with no answer recorded (the app crashed mid-request) becomes
``creation_uncertain``. Each uncertain call is then looked up on the voice platform by our
``call_id``, outside any transaction, until it is found (``dialing``; ended at once with an
apology if found more than 2 min after the attempt) or the max call length plus 5 min has
passed (``failed``).
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg_pool import AsyncConnectionPool

from app.calls import messages
from app.calls.commands import PAUSE_CAP_S, begin_ending
from app.calls.pings import close_ping, ping_for_update
from app.calls.store import (
    CONTROL_TIMEOUT,
    calling_name,
    is_ending,
    queue_control,
    set_status,
)
from app.calls_lock import CallNotFound, lock_call, write_call_secrets
from app.events import append_event
from app.registry import AppContext
from app.voice.port import CreatedCall, VoicePlatform, VoiceUncertain
from app.voice.vapi import get_voice
from app.webhooks.vapi import dispatch_pending

logger = logging.getLogger(__name__)

SWEEP_EVERY_S = 0.5
DISPATCH_EVERY_S = 5.0
# Uncertain creations are looked up on the voice platform at most this often.
RESOLVE_EVERY_S = 5.0

# A create request with no recorded answer after this long is presumed lost (a crash).
CREATE_STALL_S = 15
# A call found later than this after the attempt is adopted and ended at once (§4.1).
LATE_ADOPTION_S = 120
# Stop looking (and fail the call) this long after the longest call could have ended.
GIVE_UP_GRACE_S = 300


async def expire_due_pings(pool: AsyncConnectionPool[Any], limit: int = 50) -> int:
    """Expire every open ping whose clock has run out. Returns how many it expired.

    Each ping gets its own transaction (call lock, then ping lock). A paused ping has no
    ``expires_at`` and is never picked. Never raises: a failure is logged and the ping is
    retried on the next pass.
    """
    try:
        async with pool.connection() as conn:
            due = await (
                await conn.execute(
                    "select ping_id, call_id from public.pings where state = 'open'"
                    " and expires_at <= clock_timestamp() order by expires_at limit %s",
                    (limit,),
                )
            ).fetchall()
    except Exception:
        logger.exception("sweeper could not read due pings")
        return 0
    expired = 0
    for ping_id, call_id in due:
        try:
            if await _expire_one(pool, call_id, ping_id):
                expired += 1
        except CallNotFound:
            continue
        except psycopg.OperationalError as exc:
            logger.warning("sweeper: transient error on call %s (%s)", call_id, type(exc).__name__)
        except Exception:
            logger.exception("sweeper failed to expire a ping of call %s", call_id)
    return expired


async def _expire_one(pool: AsyncConnectionPool[Any], call_id: UUID, ping_id: UUID) -> bool:
    async with pool.connection() as conn, conn.transaction():
        await lock_call(conn, call_id)
        ping = await ping_for_update(conn, call_id, ping_id)
        if ping is None or ping.state != "open" or ping.expires_at is None:
            return False
        row = await (
            await conn.execute("select clock_timestamp() >= %s", (ping.expires_at,))
        ).fetchone()
        if not (row and row[0]):
            return False  # resumed with a fresh clock since we looked
        await close_ping(conn, ping, "expired")
        await queue_control(
            conn,
            call_id,
            CONTROL_TIMEOUT,
            {"ping_id": str(ping_id), "message": messages.add_message(messages.timeout_message())},
        )
        return True


async def end_overdue_pauses(pool: AsyncConnectionPool[Any], limit: int = 50) -> int:
    """End calls paused longer than the cap with a call-back offer (DC-11).

    ``calls.paused_at`` survives a restart, so the cap holds even if the app was down.
    Never raises. Returns how many calls it started ending.
    """
    try:
        async with pool.connection() as conn:
            due = await (
                await conn.execute(
                    "select id from public.calls where status = 'on_hold'"
                    " and paused_at <= now() - make_interval(secs => %s) limit %s",
                    (PAUSE_CAP_S, limit),
                )
            ).fetchall()
    except Exception:
        logger.exception("sweeper could not read paused calls")
        return 0
    ended = 0
    for (call_id,) in due:
        try:
            if await _end_paused(pool, call_id):
                ended += 1
        except CallNotFound:
            continue
        except psycopg.OperationalError as exc:
            logger.warning("sweeper: transient error on call %s (%s)", call_id, type(exc).__name__)
        except Exception:
            logger.exception("sweeper failed to end paused call %s", call_id)
    return ended


async def _end_paused(pool: AsyncConnectionPool[Any], call_id: UUID) -> bool:
    async with pool.connection() as conn, conn.transaction():
        call = await lock_call(conn, call_id)
        row = await (
            await conn.execute(
                "select status = 'on_hold' and paused_at <= now() - make_interval(secs => %s)"
                " from public.calls where id = %s",
                (PAUSE_CAP_S, call_id),
            )
        ).fetchone()
        if not (row and row[0]) or await is_ending(conn, call_id):
            return False
        name = await calling_name(conn, call_id)
        await append_event(
            conn,
            call_id=call_id,
            type="agent.decision",
            source="backend",
            payload={
                "situation": "The call was paused for 2 minutes",
                "action": "end_call",
                "reason": "Offered a call-back so the business wasn't left waiting.",
            },
            occurred_at=datetime.now(UTC),
            dedup_key="pause_cap",
        )
        # The outcome (Needs follow-up, DC-11) is left to post-call (Harper) until she decides
        # who writes calls.outcome for a pause-cap ending.
        await begin_ending(conn, call, messages.pause_cap_goodbye_line(name))
        return True


async def mark_stalled_creations(pool: AsyncConnectionPool[Any], limit: int = 50) -> int:
    """Move ``creating`` calls whose create request has gone unanswered for 15 s to
    ``creation_uncertain``. Returns how many it moved.

    ``POST /calls`` commits ``create_attempted_at`` before it sends the request, so such a row
    means the app stopped (or hung) before it recorded what Vapi said. Each call gets its own
    transaction under the call lock, and the move is a guarded update, so a late answer from
    the request or a second pass changes nothing. Never raises.
    """
    try:
        async with pool.connection() as conn:
            due = await (
                await conn.execute(
                    "select id from public.calls where status = 'creating'"
                    " and vapi_call_id is null and ended_at is null"
                    " and create_attempted_at <= now() - make_interval(secs => %s)"
                    " order by create_attempted_at limit %s",
                    (CREATE_STALL_S, limit),
                )
            ).fetchall()
    except Exception:
        logger.exception("sweeper could not read stalled creations")
        return 0
    moved = 0
    for (call_id,) in due:
        try:
            if await _mark_stalled(pool, call_id):
                moved += 1
                logger.warning("call %s: create request never answered; outcome unknown", call_id)
        except CallNotFound:
            continue
        except psycopg.OperationalError as exc:
            logger.warning("sweeper: transient error on call %s (%s)", call_id, type(exc).__name__)
        except Exception:
            logger.exception("sweeper failed to mark the creation of call %s uncertain", call_id)
    return moved


async def _mark_stalled(pool: AsyncConnectionPool[Any], call_id: UUID) -> bool:
    async with pool.connection() as conn, conn.transaction():
        call = await lock_call(conn, call_id)
        if call.vapi_call_id is not None or call.ended_at is not None:
            return False  # the answer was recorded since we looked
        row = await (
            await conn.execute(
                "select create_attempted_at <= now() - make_interval(secs => %s)"
                " from public.calls where id = %s",
                (CREATE_STALL_S, call_id),
            )
        ).fetchone()
        if not (row and row[0]):
            return False
        return await set_status(
            conn, call_id, "creation_uncertain", only_from=frozenset({"creating"})
        )


@dataclass(frozen=True)
class _Uncertain:
    """An uncertain call as read before the lookup. ``give_up`` is decided by the database
    clock: true once the max call length plus 5 min has passed since the attempt."""

    call_id: UUID
    create_attempted_at: datetime
    give_up: bool


async def resolve_uncertain_creations(
    pool: AsyncConnectionPool[Any], voice: VoicePlatform, limit: int = 50
) -> int:
    """Look up every ``creation_uncertain`` call on the voice platform by our ``call_id``.

    - Found: store ``vapi_call_id`` and the control URL and move it to ``dialing``. If it
      turned up more than 2 min after the attempt, the user has already been told it failed,
      so it is adopted and ended at once with a short apology, and logged.
    - Not found, and the max call length plus 5 min has passed: ``failed``.
    - Not found yet, or the lookup failed (``VoiceUncertain`` or any other error): left as
      it is and tried again on the next pass.

    The lookup runs outside any transaction; the result is recorded in one transaction under
    the call lock with guarded updates, so a webhook or a second pass that got there first
    wins. A create request is never resent. Never raises. Returns how many calls it resolved.
    """
    try:
        async with pool.connection() as conn:
            found_rows = await (
                await conn.execute(
                    "select id, create_attempted_at,"
                    " now() >= create_attempted_at + make_interval(secs => max_duration_s + %s)"
                    " from public.calls where status = 'creation_uncertain'"
                    " and ended_at is null and create_attempted_at is not null"
                    " order by create_attempted_at limit %s",
                    (GIVE_UP_GRACE_S, limit),
                )
            ).fetchall()
    except Exception:
        logger.exception("sweeper could not read uncertain creations")
        return 0
    resolved = 0
    for call_id, attempted_at, give_up in found_rows:
        uncertain = _Uncertain(call_id, attempted_at, bool(give_up))
        try:
            if await _resolve_one(pool, voice, uncertain):
                resolved += 1
        except CallNotFound:
            continue
        except psycopg.OperationalError as exc:
            logger.warning("sweeper: transient error on call %s (%s)", call_id, type(exc).__name__)
        except Exception:
            logger.exception("sweeper failed to resolve the creation of call %s", call_id)
    return resolved


async def _resolve_one(
    pool: AsyncConnectionPool[Any], voice: VoicePlatform, uncertain: _Uncertain
) -> bool:
    call_id = uncertain.call_id
    try:
        created = await voice.find_call_by_app_id(
            call_id, created_after=uncertain.create_attempted_at
        )
    except VoiceUncertain as exc:
        logger.info("call %s: creation lookup inconclusive, retrying (%s)", call_id, exc)
        return False
    except Exception as exc:
        # VoiceNotSent, VoiceRejected or anything else: we still don't know. Retry later.
        logger.warning(
            "call %s: creation lookup failed, retrying (%s)", call_id, type(exc).__name__
        )
        return False
    async with pool.connection() as conn, conn.transaction():
        if created is not None:
            return await _adopt(conn, call_id, created)
        if uncertain.give_up:
            return await _fail_uncertain(conn, call_id)
        return False


async def _adopt(conn: psycopg.AsyncConnection[Any], call_id: UUID, created: CreatedCall) -> bool:
    """Record a call the lookup found. In the caller's transaction."""
    call = await lock_call(conn, call_id)
    if call.status != "creation_uncertain" or call.ended_at is not None:
        return False  # a webhook or an earlier pass got here first
    row = await (
        await conn.execute(
            "update public.calls set vapi_call_id = %s where id = %s"
            " and (vapi_call_id is null or vapi_call_id = %s)"
            " returning now() - create_attempted_at > make_interval(secs => %s)",
            (created.vapi_call_id, call_id, created.vapi_call_id, LATE_ADOPTION_S),
        )
    ).fetchone()
    if row is None:
        # Another Vapi id is already stored: not ours to overwrite.
        logger.error("call %s: lookup found a different voice call than the one stored", call_id)
        return False
    late = bool(row[0])
    if created.control_url:
        await write_call_secrets(conn, call_id, created.control_url, created.listen_url or "")
    await set_status(conn, call_id, "dialing", only_from=frozenset({"creation_uncertain"}))
    if not late:
        logger.info("call %s: uncertain creation found; the call was placed", call_id)
        return True
    await append_event(
        conn,
        call_id=call_id,
        type="agent.decision",
        source="backend",
        payload={
            "situation": "The call was confirmed more than 2 minutes after it was started",
            "action": "end_call",
            "reason": "It had already been shown as failed, so it was ended with an apology.",
        },
        occurred_at=datetime.now(UTC),
        dedup_key="late_adoption",
    )
    if not await is_ending(conn, call_id):
        await begin_ending(
            conn, await lock_call(conn, call_id), messages.late_adoption_apology_line()
        )
    if not created.control_url:
        logger.error("call %s: adopted late with no control URL; it cannot be ended", call_id)
    logger.warning("call %s: found after more than 2 min; adopted and ended", call_id)
    return True


async def _fail_uncertain(conn: psycopg.AsyncConnection[Any], call_id: UUID) -> bool:
    """Give up on a creation that was never found: ``failed``, as ``record_not_placed`` does."""
    await lock_call(conn, call_id)
    row = await (
        await conn.execute(
            "update public.calls set status = 'failed', outcome = 'failed', ended_at = now()"
            " where id = %s and status = 'creation_uncertain' and ended_at is null"
            " returning id",
            (call_id,),
        )
    ).fetchone()
    if row is None:
        return False
    await append_event(
        conn,
        call_id=call_id,
        type="status.changed",
        source="backend",
        payload={"status": "failed"},
        occurred_at=datetime.now(UTC),
    )
    logger.warning("call %s: creation never found on the voice platform; failed", call_id)
    return True


async def sweeper(ctx: AppContext) -> None:
    """Worker: every 0.5 s expire due pings, end overdue pauses and mark stalled creations
    uncertain; every 5 s resolve uncertain creations and run pending tool calls."""
    if ctx.pool is None:
        return
    voice = get_voice(ctx.settings)
    loop = asyncio.get_running_loop()
    next_dispatch = 0.0
    next_resolve = 0.0
    while True:
        await expire_due_pings(ctx.pool)
        await end_overdue_pauses(ctx.pool)
        await mark_stalled_creations(ctx.pool)
        if loop.time() >= next_resolve:
            await resolve_uncertain_creations(ctx.pool, voice)
            next_resolve = loop.time() + RESOLVE_EVERY_S
        if loop.time() >= next_dispatch:
            await dispatch_pending(ctx.pool, tools=ctx.registries.tools)
            next_dispatch = loop.time() + DISPATCH_EVERY_S
        await asyncio.sleep(SWEEP_EVERY_S)
