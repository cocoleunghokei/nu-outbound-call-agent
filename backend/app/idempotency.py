"""Idempotency-Key support backed by the backend-only public.idempotency_keys table."""

import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Header
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.db_tx import require_transaction
from app.errors import ApiError


@dataclass(frozen=True)
class StoredResponse:
    """A response saved for a previously completed request."""

    status_code: int
    body: Any


async def idempotency_key(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> uuid.UUID:
    """FastAPI dependency: the Idempotency-Key header as a UUID, or 400."""
    try:
        if idempotency_key is not None:
            return uuid.UUID(idempotency_key)
    except ValueError:
        pass
    raise ApiError(
        "idempotency_key_required", 400, "Send an Idempotency-Key header with a UUID", False
    )


async def claim_idempotency(
    conn: AsyncConnection[Any], user_id: uuid.UUID, key: uuid.UUID
) -> StoredResponse | None:
    """Claim (user_id, key) inside the caller's transaction.

    Returns the stored response for a replay, or None when the caller should do the work and
    then call store_idempotent_response in the same transaction. Raises 409 in_progress if
    another transaction holds the claim. Creates the profile row if missing, because
    idempotency_keys.user_id references public.profiles and no trigger creates profiles.
    Raises RuntimeError if ``conn`` is in autocommit mode with no transaction open: the
    advisory lock is transaction-scoped, so it would be released at once.
    """
    require_transaction(conn, "claim_idempotency")
    await conn.execute(
        "insert into public.profiles (id) values (%s) on conflict (id) do nothing", (user_id,)
    )
    locked = await (
        await conn.execute(
            "select pg_try_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"{user_id}{key}",),
        )
    ).fetchone()
    if locked is None or not locked[0]:
        raise ApiError("in_progress", 409, "A request with this key is still running", True)

    row = await (
        await conn.execute(
            "select status_code, response from public.idempotency_keys"
            " where user_id = %s and key = %s",
            (user_id, str(key)),
        )
    ).fetchone()
    if row is not None:
        if row[0] is not None:
            return StoredResponse(status_code=row[0], body=row[1])
        return None
    await conn.execute(
        "insert into public.idempotency_keys (user_id, key) values (%s, %s)",
        (user_id, str(key)),
    )
    return None


async def store_idempotent_response(
    conn: AsyncConnection[Any], user_id: uuid.UUID, key: uuid.UUID, status_code: int, body: Any
) -> None:
    """Save the response so a replay of the same key returns it."""
    await conn.execute(
        "update public.idempotency_keys set status_code = %s, response = %s"
        " where user_id = %s and key = %s",
        (status_code, Jsonb(body), user_id, str(key)),
    )
