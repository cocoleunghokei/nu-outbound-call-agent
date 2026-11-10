"""The whole pause-and-ping loop with the real background workers running (DC-7, DC-8, DC-9).

The app uses the fake voice platform (``voice_platform="fake"``), so "sent" means the outbox
worker handed the command to the adapter.
"""

import asyncio
import time

import pytest
from fastapi import FastAPI

from app.main import create_app
from tests.calls_helpers import (
    JWT_ISSUER,
    JWT_KEY,
    ask_body,
    auth_headers,
    expire_now,
    outbox_rows,
    ping_rows,
    seed_call,
)
from tests.conftest import FreshCall, SupabaseEnv
from tests.test_webhooks import make_settings, post, serve

pytestmark = pytest.mark.db

# DC-8: the agent should speak the reply within 3 s of the tap; our share must fit well inside.
DELIVERY_BUDGET_S = 2.0


async def _wait_for_sent(db: str, call: FreshCall, kind: str, budget_s: float) -> float:
    """Seconds until the call's ``kind`` control row is sent; fails after ``budget_s``."""
    start = time.monotonic()
    while time.monotonic() - start < budget_s:
        rows = [o for o in await outbox_rows(db, call) if o["kind"] == kind]
        if rows and rows[0]["state"] == "sent":
            return time.monotonic() - start
        await asyncio.sleep(0.05)
    raise AssertionError(f"{kind} not sent within {budget_s}s: {await outbox_rows(db, call)}")


def _app(db: str) -> FastAPI:
    return create_app(
        make_settings(db, supabase_jwt_secret=JWT_KEY, supabase_jwt_issuer=JWT_ISSUER)
    )


def test_reply_reaches_the_call_within_budget(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(_app(db)) as client:
            await post(client, ask_body(call)[1])
            [ping] = await ping_rows(db, call)
            response = await client.post(
                f"/calls/{call.call_id}/pings/{ping['ping_id']}/reply",
                json={"choice": 0},
                headers=auth_headers(call.user_id),
            )
            assert response.status_code == 200
            await _wait_for_sent(db, call, "reply", DELIVERY_BUDGET_S)

    asyncio.run(scenario())


def test_unanswered_ping_times_out_and_tells_the_agent(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(_app(db)) as client:
            await post(client, ask_body(call)[1])
            await expire_now(db, call)
            await _wait_for_sent(db, call, "timeout", 3.0)
        [ping] = await ping_rows(db, call)
        assert ping["state"] == "expired"

    asyncio.run(scenario())
