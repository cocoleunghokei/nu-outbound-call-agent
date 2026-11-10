"""SQL for the ``briefs`` table and the profile snapshot.

The backend connects as the database owner, so row-level security does not apply to it
(``app/db.py``). Every query here therefore filters by ``user_id`` itself: a user can only
ever read or change their own briefs.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.briefs.readiness import Readiness, check_readiness
from app.voice.prompt_safety import Snapshot


@dataclass(frozen=True)
class StoredBrief:
    """One row of ``briefs``."""

    id: uuid.UUID
    brief: dict[str, Any]
    missing_fields: list[str]
    created_at: datetime


async def load_snapshot(conn: AsyncConnection[Any], user_id: uuid.UUID) -> Snapshot:
    """The user's profile fields as a read-only snapshot: key -> (value, sharing)."""
    rows = await (
        await conn.execute(
            "select key, value, sharing from public.profile_fields"
            " where user_id = %s and btrim(value) <> ''",
            (user_id,),
        )
    ).fetchall()
    return Snapshot.from_rows((str(k), str(v), s) for k, v, s in rows)


async def insert_brief(
    conn: AsyncConnection[Any], user_id: uuid.UUID, brief: dict[str, Any], missing: list[str]
) -> StoredBrief:
    """Insert a new brief owned by ``user_id``."""
    row = await (
        await conn.execute(
            "insert into public.briefs"
            " (user_id, goal, business_name, phone_e164, template_key, brief, missing_fields)"
            " values (%s, %s, %s, %s, %s, %s, %s)"
            " returning id, created_at",
            (
                user_id,
                brief["goal"],
                brief["business_name"],
                brief["phone_e164"],
                brief["template_key"],
                Jsonb(brief),
                Jsonb(missing),
            ),
        )
    ).fetchone()
    assert row is not None
    return StoredBrief(id=row[0], brief=brief, missing_fields=missing, created_at=row[1])


async def get_brief(
    conn: AsyncConnection[Any], user_id: uuid.UUID, brief_id: uuid.UUID, *, for_update: bool = False
) -> StoredBrief | None:
    """The brief if it exists and belongs to ``user_id``; None otherwise (never says which)."""
    sql = (
        "select id, brief, missing_fields, created_at from public.briefs"
        " where id = %s and user_id = %s"
    )
    if for_update:
        sql += " for update"
    row = await (await conn.execute(sql, (brief_id, user_id))).fetchone()
    if row is None:
        return None
    return StoredBrief(id=row[0], brief=row[1], missing_fields=list(row[2]), created_at=row[3])


async def update_brief(
    conn: AsyncConnection[Any],
    user_id: uuid.UUID,
    brief_id: uuid.UUID,
    brief: dict[str, Any],
    missing: list[str],
) -> None:
    """Replace a brief the caller already locked with ``get_brief(..., for_update=True)``."""
    await conn.execute(
        "update public.briefs set goal = %s, business_name = %s, phone_e164 = %s,"
        " template_key = %s, brief = %s, missing_fields = %s"
        " where id = %s and user_id = %s",
        (
            brief["goal"],
            brief["business_name"],
            brief["phone_e164"],
            brief["template_key"],
            Jsonb(brief),
            Jsonb(missing),
            brief_id,
            user_id,
        ),
    )


async def brief_has_call(
    conn: AsyncConnection[Any], user_id: uuid.UUID, brief_id: uuid.UUID
) -> bool:
    """True once a call was started from this brief; such a brief can no longer change."""
    row = await (
        await conn.execute(
            "select exists (select 1 from public.calls where brief_id = %s and user_id = %s)",
            (brief_id, user_id),
        )
    ).fetchone()
    return bool(row and row[0])


async def brief_readiness(
    conn: AsyncConnection[Any], user_id: uuid.UUID, brief_id: uuid.UUID
) -> Readiness | None:
    """For ``POST /calls`` (Coco): readiness of the user's brief against their profile *now*.

    Returns None when the brief doesn't exist or isn't the user's. Refuse to dial unless
    ``.ready``. Computed fresh, because the profile may have changed since the brief was saved.
    """
    stored = await get_brief(conn, user_id, brief_id)
    if stored is None:
        return None
    return check_readiness(stored.brief, await load_snapshot(conn, user_id))
