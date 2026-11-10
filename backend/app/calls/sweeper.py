"""The sweeper: closes pings nobody answered in time (DC-9) and runs pending tool calls.

The timeout loses to a reply that reached the ping's row lock first: both sides re-check the
ping under that lock against ``clock_timestamp()``, so exactly one of them closes it.
"""

import asyncio
import logging
from typing import Any
from uuid import UUID

import psycopg
from psycopg_pool import AsyncConnectionPool

from app.calls import messages
from app.calls.pings import close_ping, ping_for_update
from app.calls.store import CONTROL_TIMEOUT, queue_control
from app.calls_lock import CallNotFound, lock_call
from app.registry import AppContext
from app.webhooks.vapi import dispatch_pending

logger = logging.getLogger(__name__)

SWEEP_EVERY_S = 0.5
DISPATCH_EVERY_S = 5.0


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


async def sweeper(ctx: AppContext) -> None:
    """Worker: expire due pings every 0.5 s and run pending tool calls every 5 s."""
    if ctx.pool is None:
        return
    loop = asyncio.get_running_loop()
    next_dispatch = 0.0
    while True:
        await expire_due_pings(ctx.pool)
        if loop.time() >= next_dispatch:
            await dispatch_pending(ctx.pool, tools=ctx.registries.tools)
            next_dispatch = loop.time() + DISPATCH_EVERY_S
        await asyncio.sleep(SWEEP_EVERY_S)
