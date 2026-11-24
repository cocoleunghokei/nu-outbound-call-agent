"""The ``report_decision`` tool handler: what the agent did on its own (DC-4, DC-5).

The ingress has already moved ``confidence`` and ``dimensions`` to ``agent_scores``; this
handler only writes the browser-visible ``agent.decision`` event: the situation, the action,
the plain-language reason and, for a Medium decision, the missed preference.

Taking an offer on its own while a question is open (Q2 in ``evals/incall/CASES.md``): the
business made a better offer during the wait and the agent took it, so the open question is
settled. The handler closes that ping as ``superseded``, drops its "still checking" lines and
returns the call to Talking, so the user never answers a question that no longer applies.
Only a ``decision`` ping is closed: an Ask-me-first approval stays open, because taking an
offer doesn't answer "may I share your date of birth?". A decision older than the ping (a
late webhook) closes nothing.

When the call carries the ``offer`` the model heard, the Low-offer gate judges it with
Python's score against the brief's rules (``app/calls/scoring.py``) instead of the model's
own checks. Before anything is logged, the length gate refuses a reason or missed preference
too long for the card, and the money and Low-offer gates (``app/calls/gates.py``) may refuse
an accept or take_fallback: the agent then gets the refusal as its tool result, nothing is
logged, and the open question stays open.

Two more gates guard taking an offer: ``new_proposal_gate`` (the brief can't answer it, so
the user decides) and ``never_agree_gate`` (the business must say, in words that are really in
the transcript, that the offer isn't a never-agree item).

A field the agent shares again (the business couldn't hear the phone number) is logged once:
see ``decision_dedup_key``.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from psycopg import AsyncConnection

from app.calls import messages
from app.calls.gates import (
    SETTLING_ACTIONS,
    business_said_booked,
    counter_gate,
    load_brief,
    load_facts,
    low_offer_gate,
    missing_detail_gate,
    money_gate,
    needless_counter_gate,
    never_agree_gate,
    new_proposal_gate,
)
from app.calls.pings import close_ping, open_ping_for_update
from app.calls.scoring import score_tool_call
from app.calls.store import set_status
from app.events import append_event
from app.webhooks.registry import ToolCallContext

logger = logging.getLogger(__name__)

DECISION_FIELDS = ("situation", "action", "reason", "field_key", "missed_preference")
NOTED = "Noted."


def decision_dedup_key(payload: dict[str, str], tool_call_id: str) -> str:
    """The event's dedup key: one per tool call, except a ``share_field`` that names its field.

    A field is shared once per call as far as the user's feed goes. When the business can't
    hear the phone number and the agent says it again, that is the same share, not a new
    decision, so the repeat is not logged ("They couldn't hear the phone number. Repeating
    it slowly." was a second row for the same share). Without a ``field_key`` a share can't be
    matched to an earlier one, so it stays one row per tool call.
    """
    field_key = payload.get("field_key")
    if payload.get("action") == "share_field" and field_key:
        return f"decision:share_field:{field_key}"
    return f"decision:{tool_call_id}"


async def handle_report_decision(conn: AsyncConnection[Any], ctx: ToolCallContext) -> str:
    """Log the decision for the live feed. Returns a short acknowledgement to the model.

    An accept or take_fallback that a gate refuses is not logged; the refusal is returned.
    """
    action = ctx.arguments.get("action")
    facts = await load_facts(conn, ctx.call_id)
    if action == "counter":
        score = score_tool_call(await load_brief(conn, ctx.call_id), ctx.arguments)
        refusal = counter_gate(action, facts) or needless_counter_gate(ctx.dimensions, score)
        if refusal is not None:
            logger.info("report_decision refused by a counter gate for call %s", ctx.call_id)
            return refusal
    if action in SETTLING_ACTIONS:
        brief = await load_brief(conn, ctx.call_id)
        score = score_tool_call(brief, ctx.arguments)
        covered = ctx.arguments.get("covered_by_instruction")
        refusal = (
            money_gate(action, facts)
            or low_offer_gate(action, ctx.dimensions, facts, score, covered)
            or new_proposal_gate(action, ctx.dimensions, facts, covered)
            or missing_detail_gate(action, facts, score, covered)
            or never_agree_gate(action, ctx.arguments, brief, facts, covered)
        )
        if refusal is not None:
            logger.info("report_decision refused by a gate for call %s", ctx.call_id)
            return refusal
    payload = {
        key: value.strip()
        for key in DECISION_FIELDS
        if isinstance(value := ctx.arguments.get(key), str) and value.strip()
    }
    await append_event(
        conn,
        call_id=ctx.call_id,
        type="agent.decision",
        source="vapi",
        payload=payload,
        occurred_at=ctx.source_ts or datetime.now(UTC),
        dedup_key=decision_dedup_key(payload, ctx.tool_call_id),
    )
    if payload.get("action") not in SETTLING_ACTIONS:
        return NOTED
    withdrawn = await _withdraw_open_question(conn, ctx)
    if business_said_booked(facts):
        return messages.already_booked_result(withdrawn)
    return messages.question_withdrawn_result() if withdrawn else NOTED


async def _withdraw_open_question(conn: AsyncConnection[Any], ctx: ToolCallContext) -> bool:
    """Close the open decision ping this decision settles. True if one was closed."""
    ping = await open_ping_for_update(conn, ctx.call_id)
    if ping is None or ping.kind != "decision":
        return False
    if ctx.source_ts is not None and ctx.source_ts < ping.source_ts:
        return False
    await close_ping(conn, ping, "superseded")
    await set_status(conn, ctx.call_id, "talking", only_from=frozenset({"waiting_on_you"}))
    return True
