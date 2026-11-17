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
"""

from datetime import UTC, datetime
from typing import Any

from psycopg import AsyncConnection

from app.calls import messages
from app.calls.pings import close_ping, open_ping_for_update
from app.calls.store import set_status
from app.events import append_event
from app.webhooks.registry import ToolCallContext

DECISION_FIELDS = ("situation", "action", "reason", "field_key", "missed_preference")
NOTED = "Noted."
# Actions that settle the offer an open question was about.
SETTLING_ACTIONS = frozenset({"accept", "take_fallback"})


async def handle_report_decision(conn: AsyncConnection[Any], ctx: ToolCallContext) -> str:
    """Log the decision for the live feed. Returns a short acknowledgement to the model."""
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
        dedup_key=f"decision:{ctx.tool_call_id}",
    )
    if payload.get("action") in SETTLING_ACTIONS and await _withdraw_open_question(conn, ctx):
        return messages.question_withdrawn_result()
    return NOTED


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
