"""The control-lane outbox worker: sends queued commands to a live call's control URL.

Rows are sent strictly in id order per call, one at a time, among the rows that are due
(``not_before`` has passed), so a "still checking" line scheduled for later never holds up a
reply. Delivery rules (architecture §4.1):

- Just before sending, the row is re-checked against the call and ping state; a row the
  state has made pointless is ``cancelled``: a filler for a closed ping, a pause line after
  Resume, anything but the goodbye once the call is ending, anything for an ended call.
- While the call is paused, replies and timeouts wait (they are skipped, not cancelled) and
  Resume re-queues them after its unmute, so the user's Pause is respected (DC-11).
- A row carries one ``message`` or an ordered list of ``messages`` (unmute, say, mute).
- The adapter never retries. A send that provably never left (``VoiceNotSent``) is retried
  once; fillers and pause reassurances are never retried. A send that may have gone out
  (``VoiceUncertain``) is marked ``uncertain`` and never resent automatically.
- The network call happens outside any transaction, so the call lock is never held for it.
- ``attempts`` counts sends that may have gone out. On start-up, a row still ``pending``
  with ``attempts > 0`` may have been sent before a crash, so it becomes ``uncertain``.

Assumes one backend instance runs this worker (the Railway deploy has one).
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.calls.store import (
    CONTROL_FILLER,
    CONTROL_HOLD,
    CONTROL_REASSURE,
    ENDING_KINDS,
    HELD_WHILE_PAUSED,
    PAUSE_KINDS,
    is_ending,
)
from app.calls_lock import CallNotFound, lock_call
from app.registry import AppContext
from app.voice.port import VoiceNotSent, VoicePlatform, VoiceRejected, VoiceUncertain
from app.voice.vapi import get_voice

logger = logging.getLogger(__name__)

SEND_EVERY_S = 0.25
MAX_ROWS_PER_CALL_PER_PASS = 10
TERMINAL_STATUSES = frozenset({"ended", "failed"})
# Lines that are worse late than missing: never retried, cancelled after a restart.
AT_MOST_ONCE = frozenset({CONTROL_FILLER, CONTROL_REASSURE})


@dataclass(frozen=True)
class _Claimed:
    """A row cleared to send. ``control_url`` is a capability: never log it."""

    id: int
    call_id: UUID
    kind: str
    messages: list[dict[str, Any]]
    control_url: str
    retried: bool


async def _set_state(conn: AsyncConnection[Any], row_id: int, state: str) -> None:
    await conn.execute("update public.outbox set state = %s where id = %s", (state, row_id))


def _messages_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """A row carries one ``message`` or an ordered list of ``messages`` (e.g. unmute, say)."""
    if isinstance(payload.get("message"), dict):
        return [payload["message"]]
    found = payload.get("messages")
    if isinstance(found, list) and found and all(isinstance(m, dict) for m in found):
        return list(found)
    return []


async def _claim_next(conn: AsyncConnection[Any], call_id: UUID) -> _Claimed | None:
    """In a transaction with the call lock: pick the call's next due row, check it, claim it.

    Returns None when nothing is left to send for this call in this pass. While the call is
    paused, answers for the agent (replies, timeouts) are skipped: they wait for Resume.
    """
    call = await lock_call(conn, call_id)
    paused = call.status == "on_hold"
    ending = await is_ending(conn, call_id)
    while True:
        row = await (
            await conn.execute(
                "select id, kind, payload from public.outbox"
                " where call_id = %s and lane = 'control' and state = 'pending'"
                " and not_before <= now() and not (%s and kind = any(%s))"
                " order by id limit 1 for update",
                (call_id, paused and not ending, list(HELD_WHILE_PAUSED)),
            )
        ).fetchone()
        if row is None:
            return None
        row_id, kind, payload = row
        payload = payload if isinstance(payload, dict) else {}
        messages = _messages_of(payload)
        if call.status in TERMINAL_STATUSES or not messages:
            await _set_state(conn, row_id, "cancelled")
            continue
        if ending and kind not in ENDING_KINDS:
            await _set_state(conn, row_id, "cancelled")  # nothing but the goodbye after End
            continue
        if kind in PAUSE_KINDS and not paused:
            await _set_state(conn, row_id, "cancelled")  # resumed since it was queued
            continue
        if kind == CONTROL_FILLER and not await _filler_still_wanted(conn, call.status, payload):
            await _set_state(conn, row_id, "cancelled")
            continue
        secret = await (
            await conn.execute(
                "select control_url from public.call_secrets where call_id = %s", (call_id,)
            )
        ).fetchone()
        if secret is None:
            # No live control URL (not connected yet, or already gone): nothing can be sent.
            await _set_state(conn, row_id, "cancelled" if kind == CONTROL_FILLER else "failed")
            logger.warning("outbox: call %s has no control URL; %s row not sent", call_id, kind)
            continue
        await conn.execute(
            "update public.outbox set attempts = attempts + 1 where id = %s", (row_id,)
        )
        return _Claimed(
            id=row_id,
            call_id=call_id,
            kind=kind,
            messages=messages,
            control_url=secret[0],
            retried=bool(payload.get("retried")),
        )


async def _filler_still_wanted(
    conn: AsyncConnection[Any], status: str, payload: dict[str, Any]
) -> bool:
    """A "still checking" line is only spoken while its ping is open and the call isn't paused."""
    if status == "on_hold":
        return False
    row = await (
        await conn.execute(
            "select 1 from public.pings where ping_id::text = %s and state = 'open'",
            (str(payload.get("ping_id", "")),),
        )
    ).fetchone()
    return row is not None


async def _record_result(
    pool: AsyncConnectionPool[Any], claimed: _Claimed, error: Exception | None
) -> bool:
    """Store how the send went. Returns True if the row is done (sent, failed or uncertain)."""
    async with pool.connection() as conn, conn.transaction():
        if error is None:
            # The quiet rule (speech.py) needs to know when the hold line went out.
            await conn.execute(
                "update public.outbox set state = 'sent', payload = case when kind = %s"
                " then payload || jsonb_build_object('sent_at', now()) else payload end"
                " where id = %s",
                (CONTROL_HOLD, claimed.id),
            )
            return True
        if (
            isinstance(error, VoiceNotSent)
            and claimed.kind != CONTROL_FILLER
            and not claimed.retried
        ):
            # It never left: undo the attempt and allow exactly one retry.
            await conn.execute(
                "update public.outbox set attempts = attempts - 1,"
                " payload = payload || %s where id = %s",
                (Jsonb({"retried": True}), claimed.id),
            )
            return False
        state = "uncertain" if isinstance(error, VoiceUncertain) else "failed"
        await _set_state(conn, claimed.id, state)
        logger.warning(
            "outbox: %s row for call %s is %s (%s)",
            claimed.kind,
            claimed.call_id,
            state,
            type(error).__name__,
        )
        return True


async def send_due_controls(pool: AsyncConnectionPool[Any], voice: VoicePlatform) -> int:
    """One pass over every call with due control rows. Returns how many rows were sent."""
    async with pool.connection() as conn:
        call_ids = [
            r[0]
            for r in await (
                await conn.execute(
                    "select distinct call_id from public.outbox where lane = 'control'"
                    " and state = 'pending' and not_before <= now() limit 100"
                )
            ).fetchall()
        ]
    sent = 0
    for call_id in call_ids:
        for _ in range(MAX_ROWS_PER_CALL_PER_PASS):
            try:
                async with pool.connection() as conn, conn.transaction():
                    claimed = await _claim_next(conn, call_id)
            except CallNotFound:
                break
            if claimed is None:
                break
            error: Exception | None = None
            try:
                for index, message in enumerate(claimed.messages):
                    try:
                        await voice.send_control(claimed.control_url, message)
                    except VoiceNotSent as exc:
                        # Part of the row already went out: a retry could repeat it.
                        raise (VoiceUncertain("partly sent") if index else exc) from None
            except (VoiceNotSent, VoiceUncertain, VoiceRejected) as exc:
                error = exc
            except Exception as exc:  # an adapter bug: treat as "may have gone out"
                logger.exception("outbox: unexpected error sending for call %s", call_id)
                error = VoiceUncertain(type(exc).__name__)
            done = await _record_result(pool, claimed, error)
            if error is None:
                sent += 1
            if not done or error is not None:
                break  # keep order: nothing after a failed row goes out in this pass
    return sent


async def mark_interrupted_sends(pool: AsyncConnectionPool[Any]) -> int:
    """Start-up: a pending row that may already have been sent becomes ``uncertain``.

    Fillers are cancelled instead: a late "still checking" line is worse than none.
    Returns how many rows changed.
    """
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "update public.outbox set state = case when kind = any(%s) then 'cancelled'"
            " else 'uncertain' end"
            " where lane = 'control' and state = 'pending' and attempts > 0",
            (list(AT_MOST_ONCE),),
        )
        return int(cur.rowcount)


async def recover_outbox(ctx: AppContext) -> None:
    """Startup hook: see ``mark_interrupted_sends``. Never raises, so it can't stop start-up."""
    if ctx.pool is None:
        return
    try:
        changed = await mark_interrupted_sends(ctx.pool)
    except Exception:
        logger.exception("outbox: could not check for interrupted sends at start-up")
        return
    if changed:
        logger.warning("outbox: %d control rows interrupted by a restart were marked", changed)


async def control_worker(ctx: AppContext) -> None:
    """Worker: send due control rows every 0.25 s."""
    if ctx.pool is None:
        return
    voice = get_voice(ctx.settings)
    while True:
        try:
            await send_due_controls(ctx.pool, voice)
        except Exception:
            logger.exception("outbox pass failed")
        await asyncio.sleep(SEND_EVERY_S)
