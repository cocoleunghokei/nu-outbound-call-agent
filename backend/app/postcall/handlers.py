"""Vapi handlers for the end of a call: ``report_outcome`` (tool) and ``end-of-call-report``.

Both run inside the ingress transaction with the call lock held, so they must not commit
and must not call the network. The slow part, deciding the outcome and checking what was
said, is queued on the outbox ``jobs`` lane and done by the worker in ``jobs.py``.
"""

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.calls.gates import give_up_gate, load_facts, unsaid_name_gate
from app.calls_lock import end_call_transition
from app.webhooks.bind import BoundCall, message_of
from app.webhooks.normalise import ROLE_MAP
from app.webhooks.registry import ToolCallContext

# Q12: say goodbye, then wait for theirs. "You can end the call" here made the agent hang up
# before the business's goodbye (T-01 in eval 2026-11-18T0319Z).
NOTED = "Noted. Say thanks and goodbye, then end the call only after the business says goodbye."

JOB_OUTCOME = "outcome"
JOB_SHARED_FIELDS = "shared_fields"
POSTCALL_JOBS = (JOB_OUTCOME, JOB_SHARED_FIELDS)


async def handle_report_outcome(conn: AsyncConnection[Any], ctx: ToolCallContext) -> str:
    """Acknowledge the agent's outcome report, unless the give-up gate refuses it.

    The validated arguments are already stored on the ``tool_calls`` receipt; the outcome
    job reads them after the call, checks them against the transcript and decides. A
    ``needs_follow_up`` before the user was ever asked, or a staff name the business never
    said, is refused (``app/calls/gates.py``):
    the receipt keeps the refusal as its result, and ``jobs.latest_report`` skips it.
    """
    outcome = ctx.arguments.get("outcome")
    if isinstance(outcome, str):
        facts = await load_facts(conn, ctx.call_id)
        refusal = give_up_gate(outcome, facts) or unsaid_name_gate(ctx.arguments, facts)
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
    payload = {"ended_reason": ended_reason, **report_lines(message)}
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
