"""The control-lane outbox worker: order, pre-send checks and delivery rules (architecture §4.1)."""

import asyncio
from typing import Any

import psycopg
import pytest

from app.calls.outbox import mark_interrupted_sends, send_due_controls
from app.voice.fake import FakeVoicePlatform
from app.voice.port import VoiceNotSent, VoiceRejected, VoiceUncertain
from tests.calls_helpers import (
    CONTROL_URL_PREFIX,
    ask_body,
    auth_headers,
    make_app,
    make_due,
    outbox_rows,
    ping_rows,
    seed_call,
)
from tests.conftest import FreshCall, SupabaseEnv
from tests.test_webhooks import post, serve

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def quiet_outbox(supabase_env: SupabaseEnv) -> None:
    """Cancel control rows other tests left pending, so each pass here only sees this test's.

    Runs against the local test database only.
    """
    with psycopg.connect(supabase_env.db_url, autocommit=True) as conn:
        conn.execute(
            "update public.outbox set state = 'cancelled' where lane = 'control'"
            " and state = 'pending'"
        )


async def _answer(client: Any, db: str, call: FreshCall, choice: int = 0) -> None:
    """Answer the call's open ping through the API."""
    [ping_id] = [p["ping_id"] for p in await ping_rows(db, call) if p["state"] == "open"]
    response = await client.post(
        f"/calls/{call.call_id}/pings/{ping_id}/reply",
        json={"choice": choice},
        headers=auth_headers(call.user_id),
    )
    assert response.status_code == 200


def sent_to(voice: FakeVoicePlatform, call: FreshCall) -> list[tuple[str, dict[str, Any]]]:
    """What reached this call. A pass also sends rows other tests left due on their calls."""
    prefix = f"{CONTROL_URL_PREFIX}{call.call_id}/"
    return [(url, message) for url, message in voice.controls if url.startswith(prefix)]


def _kinds_sent(voice: FakeVoicePlatform, call: FreshCall) -> list[str]:
    return [message["type"] for _, message in sent_to(voice, call)]


def test_reply_is_sent_and_waiting_fillers_are_not(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    voice = FakeVoicePlatform()

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call)[1])
            await send_due_controls(app.state.pool, voice)
            assert sent_to(voice, call) == []  # fillers not due yet
            await _answer(client, db, call)
            await send_due_controls(app.state.pool, voice)
            assert len(sent_to(voice, call)) == 1
            await make_due(db, call)
            await send_due_controls(app.state.pool, voice)  # fillers were cancelled
        [(url, message)] = sent_to(voice, call)
        assert url.startswith(f"{CONTROL_URL_PREFIX}{call.call_id}/control/")
        assert message["type"] == "add-message"
        states = {
            o["kind"]: o["state"] for o in await outbox_rows(db, call) if o["lane"] == "control"
        }
        assert states == {"filler": "cancelled", "reply": "sent"}

    asyncio.run(scenario())


def test_fillers_are_spoken_while_the_ping_is_open(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    voice = FakeVoicePlatform()

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call)[1])
            await make_due(db, call)
            await send_due_controls(app.state.pool, voice)
        assert _kinds_sent(voice, call) == ["say", "say"]

    asyncio.run(scenario())


def test_fillers_are_dropped_while_the_call_is_paused(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    voice = FakeVoicePlatform()

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call)[1])
            async with await psycopg.AsyncConnection.connect(db, autocommit=True) as conn:
                await conn.execute(
                    "update public.calls set status = 'on_hold' where id = %s", (call.call_id,)
                )
            await make_due(db, call)
            await send_due_controls(app.state.pool, voice)
        assert sent_to(voice, call) == []

    asyncio.run(scenario())


def test_rows_go_out_in_order_one_at_a_time(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    voice = FakeVoicePlatform()

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call, question="Q1", at_ms=1794081610000)[1])
            await _answer(client, db, call)
            await post(client, ask_body(call, question="Q2", at_ms=1794081620000)[1])
            await _answer(client, db, call, choice=1)
            await send_due_controls(app.state.pool, voice)
        contents = [m["message"]["content"] for _, m in sent_to(voice, call)]
        assert contents == [
            'Alex\'s answer to "Q1": Ask for Fri',
            'Alex\'s answer to "Q2": Accept 4pm',
        ]

    asyncio.run(scenario())


def test_a_send_that_never_left_is_retried_once(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    voice = FakeVoicePlatform()

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call)[1])
            await _answer(client, db, call)
            voice.fail_next(VoiceNotSent("refused"))
            await send_due_controls(app.state.pool, voice)
            [reply] = [o for o in await outbox_rows(db, call) if o["kind"] == "reply"]
            assert (reply["state"], reply["attempts"]) == ("pending", 0)
            await send_due_controls(app.state.pool, voice)
            assert len(sent_to(voice, call)) == 1
        [reply] = [o for o in await outbox_rows(db, call) if o["kind"] == "reply"]
        assert (reply["state"], reply["attempts"]) == ("sent", 1)

    asyncio.run(scenario())


def test_a_second_not_sent_fails_the_row(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    voice = FakeVoicePlatform()

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call)[1])
            await _answer(client, db, call)
            voice.fail_next(VoiceNotSent("refused"))
            voice.fail_next(VoiceNotSent("refused again"))
            await send_due_controls(app.state.pool, voice)
            await send_due_controls(app.state.pool, voice)
            await send_due_controls(app.state.pool, voice)
        [reply] = [o for o in await outbox_rows(db, call) if o["kind"] == "reply"]
        assert reply["state"] == "failed"
        assert sent_to(voice, call) == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("error", "state"),
    [(VoiceUncertain("timeout"), "uncertain"), (VoiceRejected(400), "failed")],
)
def test_uncertain_or_rejected_sends_are_never_retried(
    supabase_env: SupabaseEnv, error: Exception, state: str
) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    voice = FakeVoicePlatform()

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call)[1])
            await _answer(client, db, call)
            voice.fail_next(error)
            await send_due_controls(app.state.pool, voice)
            await send_due_controls(app.state.pool, voice)
        [reply] = [o for o in await outbox_rows(db, call) if o["kind"] == "reply"]
        assert reply["state"] == state
        assert sent_to(voice, call) == []

    asyncio.run(scenario())


def test_a_filler_is_never_retried(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    voice = FakeVoicePlatform()

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call)[1])
            await make_due(db, call)
            voice.fail_next(VoiceNotSent("refused"))
            await send_due_controls(app.state.pool, voice)
        fillers = [o for o in await outbox_rows(db, call) if o["kind"] == "filler"]
        assert [f["state"] for f in fillers] == ["failed", "pending"]

    asyncio.run(scenario())


def test_nothing_is_sent_once_the_call_has_ended(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    voice = FakeVoicePlatform()

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call)[1])
            await _answer(client, db, call)
            async with await psycopg.AsyncConnection.connect(db, autocommit=True) as conn:
                await conn.execute(
                    "update public.calls set status = 'ended' where id = %s", (call.call_id,)
                )
            await send_due_controls(app.state.pool, voice)
        assert sent_to(voice, call) == []
        [reply] = [o for o in await outbox_rows(db, call) if o["kind"] == "reply"]
        assert reply["state"] == "cancelled"

    asyncio.run(scenario())


def test_a_reply_with_no_control_url_fails_instead_of_waiting(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    voice = FakeVoicePlatform()

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call)[1])
            await _answer(client, db, call)
            async with await psycopg.AsyncConnection.connect(db, autocommit=True) as conn:
                await conn.execute(
                    "delete from public.call_secrets where call_id = %s", (call.call_id,)
                )
            await send_due_controls(app.state.pool, voice)
        [reply] = [o for o in await outbox_rows(db, call) if o["kind"] == "reply"]
        assert reply["state"] == "failed"

    asyncio.run(scenario())


def test_restart_marks_a_possibly_sent_row_uncertain(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call)[1])
            await _answer(client, db, call)
            # Simulate a crash between claiming the row and recording the result.
            async with await psycopg.AsyncConnection.connect(db, autocommit=True) as conn:
                await conn.execute(
                    "update public.outbox set attempts = 1 where call_id = %s and lane = 'control'",
                    (call.call_id,),
                )
            assert await mark_interrupted_sends(app.state.pool) >= 1
        states = {
            o["kind"]: o["state"] for o in await outbox_rows(db, call) if o["lane"] == "control"
        }
        assert states == {"reply": "uncertain", "filler": "cancelled"}

    asyncio.run(scenario())
