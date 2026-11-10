"""Per-user limit on LLM brief builds: 10 per rolling hour (architecture §5.4).

Counted in Postgres (``public.brief_builds``, backend-only), so the limit survives restarts
and holds across instances. Only builds that actually call the LLM count: template builds,
and custom builds while no key is configured, never reach here. A build that then times out
still counts, because the provider was called.
"""

import math
import uuid
from typing import Any

from psycopg import AsyncConnection

from app.db_tx import require_transaction
from app.errors import ApiError

LLM_BUILDS_PER_HOUR = 10


async def reserve_llm_build(
    conn: AsyncConnection[Any], user_id: uuid.UUID, *, limit: int = LLM_BUILDS_PER_HOUR
) -> None:
    """Record one LLM build for ``user_id``, or raise 429 ``rate_limited`` (retryable).

    Must run inside the caller's transaction: a per-user advisory transaction lock makes the
    count-then-insert race-safe, so two parallel requests can't both take the last slot.
    Rows older than an hour are deleted on the way. Creates the profile row if missing,
    because ``brief_builds.user_id`` references ``public.profiles`` (like idempotency.py).
    """
    require_transaction(conn, "reserve_llm_build")
    await conn.execute(
        "insert into public.profiles (id) values (%s) on conflict (id) do nothing", (user_id,)
    )
    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"brief_builds:{user_id}",)
    )
    await conn.execute(
        "delete from public.brief_builds"
        " where user_id = %s and created_at <= now() - interval '1 hour'",
        (user_id,),
    )
    row = await (
        await conn.execute(
            "select count(*),"
            " extract(epoch from min(created_at) + interval '1 hour' - now())::float8"
            " from public.brief_builds where user_id = %s",
            (user_id,),
        )
    ).fetchone()
    assert row is not None
    used, seconds_left = int(row[0]), row[1]
    if used >= limit:
        minutes = max(1, math.ceil((seconds_left or 0) / 60))
        raise ApiError(
            "rate_limited",
            429,
            f"You've used the AI step {limit} times in the last hour. Try again in about"
            f" {minutes} min, or continue with your own words.",
            True,
        )
    await conn.execute("insert into public.brief_builds (user_id) values (%s)", (user_id,))
