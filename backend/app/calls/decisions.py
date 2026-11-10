"""The ``report_decision`` tool handler: what the agent did on its own (DC-4, DC-5).

The ingress has already moved ``confidence`` and ``dimensions`` to ``agent_scores``; this
handler only writes the browser-visible ``agent.decision`` event: the situation, the action,
the plain-language reason and, for a Medium decision, the missed preference.
"""

from datetime import UTC, datetime
from typing import Any

from psycopg import AsyncConnection

from app.events import append_event
from app.webhooks.registry import ToolCallContext

DECISION_FIELDS = ("situation", "action", "reason", "field_key", "missed_preference")
NOTED = "Noted."


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
    return NOTED
