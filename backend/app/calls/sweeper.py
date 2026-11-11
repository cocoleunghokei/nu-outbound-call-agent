"""The sweeper: closes pings nobody answered in time (DC-9), ends calls paused past the cap
(DC-11), runs pending tool calls and settles calls whose creation had no clear result
(``app/calls/reconcile.py``).

The timeout loses to a reply that reached the ping's row lock first: both sides re-check the
ping under that lock against ``clock_timestamp()``, so exactly one of them closes it.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg_pool import AsyncConnectionPool

from app.calls import messages
from app.calls.commands import PAUSE_CAP_S, begin_ending
from app.calls.pings import close_ping, ping_for_update
from app.calls.reconcile import mark_stale_creating, resolve_uncertain
from app.calls.store import CONTROL_TIMEOUT, calling_name, is_ending, queue_control
from app.calls_lock import CallNotFound, lock_call
from app.events import append_event
from app.registry import AppContext
from app.voice.vapi import get_voice
from app.webhooks.vapi import dispatch_pending

logger = logging.getLogger(__name__)

SWEEP_EVERY_S = 0.5
DISPATCH_EVERY_S = 5.0
RECONCILE_EVERY_S = 10.0


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


async def sweeper(ctx: AppContext) -> None:
    """Worker: every 0.5 s expire due pings and end overdue pauses; every 5 s run pending
    tool calls; every 10 s settle calls whose creation had no clear result."""
    if ctx.pool is None:
        return
    voice = get_voice(ctx.settings)
    loop = asyncio.get_running_loop()
    next_dispatch = 0.0
    next_reconcile = 0.0
    while True:
        await expire_due_pings(ctx.pool)
        await end_overdue_pauses(ctx.pool)
        if loop.time() >= next_dispatch:
            await dispatch_pending(ctx.pool, tools=ctx.registries.tools)
            next_dispatch = loop.time() + DISPATCH_EVERY_S
        if loop.time() >= next_reconcile:
            await mark_stale_creating(ctx.pool)
            await resolve_uncertain(ctx.pool, voice)
            next_reconcile = loop.time() + RECONCILE_EVERY_S
        await asyncio.sleep(SWEEP_EVERY_S)
