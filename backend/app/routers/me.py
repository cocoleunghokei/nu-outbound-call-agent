"""GET /me: the example route that combines auth and the database."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from psycopg_pool import AsyncConnectionPool

from app.auth import current_user
from app.db import get_pool

router = APIRouter()


@router.get("/me")
async def me(
    user_id: Annotated[uuid.UUID, Depends(current_user)],
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
) -> dict[str, str | bool]:
    """Return the caller's id and onboarding state, creating the profile row on first call."""
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.profiles (id) values (%s) on conflict (id) do nothing",
            (user_id,),
        )
        row = await (
            await conn.execute("select onboarded_at from public.profiles where id = %s", (user_id,))
        ).fetchone()
    onboarded = row is not None and row[0] is not None
    return {"user_id": str(user_id), "onboarded": onboarded}
