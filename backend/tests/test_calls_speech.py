"""The quiet rule that times the mute after Pause (DC-11, architecture §4.1)."""

import asyncio
from typing import Any

import httpx
import psycopg
import pytest

from app.calls.outbox import send_due_controls
from app.voice.fake import FakeVoicePlatform
from tests.calls_helpers import auth_headers, make_app, outbox_rows, seed_call
from tests.conftest import FreshCall, SupabaseEnv
from tests.test_webhooks import post, serve

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def quiet_outbox(supabase_env: SupabaseEnv) -> None:
    """Cancel control rows other tests left pending (local test database only)."""
    with psycopg.connect(supabase_env.db_url, autocommit=True) as conn:
        conn.execute(
            "update public.outbox set state = 'cancelled' where lane = 'control'"
            " and state = 'pending'"
        )


def speech(call: FreshCall, status: str, role: str = "assistant") -> dict[str, Any]:
    return {
        "message": {
            "type": "speech-update",
            "status": status,
            "role": role,
            "turn": 1,
            "timestamp": 1794081601000,
            "call": {"id": f"vapi-{call.call_id}", "metadata": {"call_id": str(call.call_id)}},
        }
    }


async def pause(client: httpx.AsyncClient, call: FreshCall) -> None:
    response = await client.post(
        f"/calls/{call.call_id}/commands",
        json={"type": "pause"},
        headers=auth_headers(call.user_id),
    )
    assert response.status_code == 200


async def mute_due_in(db: str, call: FreshCall) -> float:
    [mute] = [o for o in await outbox_rows(db, call) if o["kind"] == "mute"]
    return float(mute["due_in_s"])


def test_mute_waits_for_the_deadline_until_the_hold_line_is_sent(
    supabase_env: SupabaseEnv,
) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await pause(client, call)
            # The hold line hasn't gone out yet: a "stopped" is from earlier speech.
            await post(client, speech(call, "stopped"))
            assert 3 < await mute_due_in(db, call) <= 4

    asyncio.run(scenario())


def test_silence_after_the_hold_line_mutes_after_700_ms(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    voice = FakeVoicePlatform()

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await pause(client, call)
            await send_due_controls(app.state.pool, voice)  # sends the hold line
            await post(client, speech(call, "stopped"))
            assert 0.4 < await mute_due_in(db, call) <= 0.7

    asyncio.run(scenario())


def test_speaking_again_pushes_the_mute_back_to_its_deadline(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    voice = FakeVoicePlatform()

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await pause(client, call)
            await send_due_controls(app.state.pool, voice)
            await post(client, speech(call, "stopped"))
            await post(client, speech(call, "started"))
            assert await mute_due_in(db, call) > 2.5
            await post(client, speech(call, "stopped"))
            assert await mute_due_in(db, call) <= 0.7

    asyncio.run(scenario())


def test_the_business_speaking_does_not_move_the_mute(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    voice = FakeVoicePlatform()

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await pause(client, call)
            await send_due_controls(app.state.pool, voice)
            await post(client, speech(call, "stopped", role="user"))
            assert await mute_due_in(db, call) > 2.5

    asyncio.run(scenario())


def test_speech_when_not_paused_changes_nothing(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await post(client, speech(call, "stopped"))
            await post(client, speech(call, "started"))
        assert [o for o in await outbox_rows(db, call) if o["lane"] == "control"] == []

    asyncio.run(scenario())
