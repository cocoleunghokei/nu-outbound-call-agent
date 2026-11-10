"""The call status machine for Vapi's ``status-update`` messages (DC-14).

Vapi reports where the phone call is (queued, ringing, in progress, ended). Our own states
sit on top: ``waiting_on_you`` comes from an open ping and ``on_hold`` from a user pause, never
from Vapi. So Vapi's ``in-progress`` only moves a call that hasn't connected yet, and a late
or repeated message never moves a call backwards or revives an ended one.

Event handlers run again on every redelivery, so every change here is a guarded UPDATE.
"""

from typing import Any

from psycopg import AsyncConnection

from app.calls.store import set_status
from app.calls_lock import end_call_transition
from app.webhooks.bind import BoundCall, message_of

BEFORE_CONNECT = frozenset({"creating", "creation_uncertain"})


async def handle_status_update(
    conn: AsyncConnection[Any], call: BoundCall, body: dict[str, Any]
) -> None:
    """Apply a Vapi ``status-update``. Unknown statuses are ignored (kept in the raw log)."""
    status = message_of(body).get("status")
    if status in ("queued", "ringing"):
        await set_status(conn, call.call_id, "dialing", only_from=BEFORE_CONNECT)
    elif status == "in-progress":
        await set_status(
            conn, call.call_id, "talking", only_from=BEFORE_CONNECT | {"dialing", "in_phone_menu"}
        )
    elif status == "ended":
        await end_call_transition(conn, call.call_id, reason="status-update", source="vapi")
