"""Bind a Vapi webhook body to one of our calls."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection


@dataclass(frozen=True)
class BoundCall:
    """The call a webhook belongs to, as stored when the webhook arrived."""

    call_id: UUID
    user_id: UUID
    vapi_call_id: str | None
    ended_at: datetime | None


def message_of(body: Any) -> dict[str, Any]:
    """Return ``body["message"]`` if it is an object, else an empty dict."""
    message = body.get("message") if isinstance(body, dict) else None
    return message if isinstance(message, dict) else {}


def vapi_call_of(body: Any) -> dict[str, Any]:
    """Return ``body["message"]["call"]`` if it is an object, else an empty dict."""
    call = message_of(body).get("call")
    return call if isinstance(call, dict) else {}


def vapi_call_id_of(body: Any) -> str | None:
    """Return Vapi's call id (an opaque string), if the body carries one."""
    value = vapi_call_of(body).get("id")
    return value if isinstance(value, str) and value else None


def _metadata_call_id(body: Any) -> UUID | None:
    metadata = vapi_call_of(body).get("metadata")
    raw = metadata.get("call_id") if isinstance(metadata, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


async def bind_call(conn: AsyncConnection[Any], body: Any) -> BoundCall | None:
    """Find our call for a Vapi body, or None if it is unknown or does not match.

    Uses ``message.call.metadata.call_id`` (set by us when the call is created), falling back
    to a lookup of ``message.call.id`` against ``calls.vapi_call_id``. When the stored
    ``vapi_call_id`` is non-null it must equal the body's ``message.call.id``; a mismatch (or
    a missing id) returns None, so a body can never be attached to someone else's call. Takes
    no lock; the ingress calls ``lock_call`` next.
    """
    vapi_id = vapi_call_id_of(body)
    call_id = _metadata_call_id(body)
    if call_id is not None:
        row = await (
            await conn.execute(
                "select id, user_id, vapi_call_id, ended_at from public.calls where id = %s",
                (call_id,),
            )
        ).fetchone()
    elif vapi_id is not None:
        row = await (
            await conn.execute(
                "select id, user_id, vapi_call_id, ended_at from public.calls"
                " where vapi_call_id = %s",
                (vapi_id,),
            )
        ).fetchone()
    else:
        return None
    if row is None:
        return None
    stored_vapi_id: str | None = row[2]
    if stored_vapi_id is not None and stored_vapi_id != vapi_id:
        return None
    return BoundCall(call_id=row[0], user_id=row[1], vapi_call_id=stored_vapi_id, ended_at=row[3])
