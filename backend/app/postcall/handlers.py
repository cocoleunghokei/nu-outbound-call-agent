"""Vapi handlers for the end of a call: ``report_outcome`` (tool) and ``end-of-call-report``.

Both run inside the ingress transaction with the call lock held, so they must not commit
and must not call the network. The slow part, deciding the outcome and checking what was
said, is queued on the outbox ``jobs`` lane and done by the worker in ``jobs.py``.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.calls.gates import (
    give_up_gate,
    load_brief,
    load_facts,
    report_day_gate,
    unsaid_name_gate,
)
from app.calls.prompt import us_today
from app.calls.requested_day import requested_day, rule_days
from app.calls_lock import end_call_transition
from app.webhooks.bind import BoundCall, message_of
from app.webhooks.normalise import ROLE_MAP
from app.webhooks.registry import ToolCallContext

# Q12 (revised): say goodbye, then end the call. Waiting for the business's goodbye is parked:
# the model ignored it, and asking it to wait left T-02, T-03, T-06 and SM-06 never ending
# (eval 2026-11-18T0411Z).
NOTED = "Noted. Say thanks and goodbye, then end the call."

# The speakers whose lines the post-call jobs read (the app's own user never speaks on a call).
TURN_ROLES = frozenset({"agent", "business"})

JOB_OUTCOME = "outcome"
JOB_SHARED_FIELDS = "shared_fields"
POSTCALL_JOBS = (JOB_OUTCOME, JOB_SHARED_FIELDS)


async def handle_report_outcome(conn: AsyncConnection[Any], ctx: ToolCallContext) -> str:
    """Acknowledge the agent's outcome report, unless the give-up gate refuses it.

    The validated arguments are already stored on the ``tool_calls`` receipt; the outcome
    job reads them after the call, checks them against the transcript and decides. A
    ``needs_follow_up`` before the user was ever asked, a staff name the business never
    said, or a date that contradicts the day the user asked for, is refused
    (``app/calls/gates.py``):
    the receipt keeps the refusal as its result, and ``jobs.latest_report`` skips it.
    """
    outcome = ctx.arguments.get("outcome")
    if isinstance(outcome, str):
        facts = await load_facts(conn, ctx.call_id)
        brief = await load_brief(conn, ctx.call_id)
        asked_for = requested_day(brief, us_today(datetime.now(UTC)))
        refusal = (
            give_up_gate(outcome, facts)
            or unsaid_name_gate(ctx.arguments, facts)
            or report_day_gate(ctx.arguments, asked_for, rule_days(brief), facts)
        )
        if refusal is not None:
            return refusal
    return NOTED


async def handle_end_of_call_report(
    conn: AsyncConnection[Any], call: BoundCall, body: dict[str, Any]
) -> None:
    """End the call (if ``status-update: ended`` hasn't already) and queue the post-call jobs.

    Runs again on every redelivery: the end transition is a no-op once the call has ended,
    and each job is queued at most once per call.
    """
    message = message_of(body)
    reason = message.get("endedReason")
    ended_reason = reason.strip() if isinstance(reason, str) else ""
    await end_call_transition(conn, call.call_id, reason="end-of-call-report", source="vapi")
    payload = {
        "ended_reason": ended_reason,
        **report_lines(message),
        "turns": report_turns(message),
    }
    for kind in POSTCALL_JOBS:
        await queue_job(conn, call.call_id, kind, payload)


def report_lines(message: dict[str, Any]) -> dict[str, list[str]]:
    """The full transcript from the report (``artifact.messages``), split by speaker.

    The jobs use it together with the ``transcript.final`` events, so a transcript webhook
    that arrives late or not at all doesn't change the outcome. Roles use the ingress's
    mapping (bot/assistant -> agent, user/customer -> business); others are left out.
    """
    artifact = message.get("artifact")
    entries = artifact.get("messages") if isinstance(artifact, dict) else None
    lines: dict[str, list[str]] = {"agent_lines": [], "business_lines": []}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        vapi_role = entry.get("role")
        role = ROLE_MAP.get(vapi_role) if isinstance(vapi_role, str) else None
        text = entry.get("message")
        if role is not None and isinstance(text, str) and text.strip():
            lines[f"{role}_lines"].append(text.strip())
    return lines


def report_turns(message: dict[str, Any]) -> list[list[str]]:
    """The full transcript from the report in the order it was spoken, as [role, text] pairs
    (JSON has no tuples). The roles are the agent's and the business's, as in ``report_lines``.
    The outcome job needs the order to match a plain "yes" to what it answered."""
    artifact = message.get("artifact")
    entries = artifact.get("messages") if isinstance(artifact, dict) else None
    turns: list[list[str]] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        vapi_role = entry.get("role")
        role = ROLE_MAP.get(vapi_role) if isinstance(vapi_role, str) else None
        text = entry.get("message")
        if role in TURN_ROLES and isinstance(text, str) and text.strip():
            turns.append([role, text.strip()])
    return turns


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
