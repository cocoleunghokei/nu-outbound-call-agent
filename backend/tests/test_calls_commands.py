"""POST /calls/{call_id}/commands: Pause, Resume and End call (DC-11), and the pause cap."""

import asyncio
import uuid
from typing import Any

import httpx
import psycopg
import pytest

from app.calls.outbox import send_due_controls
from app.calls.sweeper import end_overdue_pauses, expire_due_pings
from app.voice.fake import FakeVoicePlatform
from tests.calls_helpers import (
    CONTROL_URL_PREFIX,
    ask_body,
    auth_headers,
    call_status,
    event_rows,
    expire_now,
    make_app,
    make_due,
    outbox_rows,
    ping_rows,
    seed_call,
)
from tests.conftest import FreshCall, SupabaseEnv, create_fresh_call
from tests.test_webhooks import post, rows, serve

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def quiet_outbox(supabase_env: SupabaseEnv) -> None:
    """Cancel control rows other tests left pending (local test database only)."""
    with psycopg.connect(supabase_env.db_url, autocommit=True) as conn:
        conn.execute(
            "update public.outbox set state = 'cancelled' where lane = 'control'"
            " and state = 'pending'"
        )


async def command(client: httpx.AsyncClient, call: FreshCall, type_: str) -> httpx.Response:
    return await client.post(
        f"/calls/{call.call_id}/commands", json={"type": type_}, headers=auth_headers(call.user_id)
    )


async def control(db: str, call: FreshCall) -> list[dict[str, Any]]:
    return [o for o in await outbox_rows(db, call) if o["lane"] == "control"]


def sent_to(voice: FakeVoicePlatform, call: FreshCall) -> list[dict[str, Any]]:
    prefix = f"{CONTROL_URL_PREFIX}{call.call_id}/"
    return [message for url, message in voice.controls if url.startswith(prefix)]


async def set_paused_at(db: str, call: FreshCall, seconds_ago: int) -> None:
    async with await psycopg.AsyncConnection.connect(db, autocommit=True) as conn:
        await conn.execute(
            "update public.calls set paused_at = now() - make_interval(secs => %s) where id = %s",
            (seconds_ago, call.call_id),
        )


def test_pause_holds_mutes_and_reassures(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            response = await command(client, call, "pause")
        assert response.status_code == 200
        assert response.json() == {
            "call_id": str(call.call_id),
            "command": "pause",
            "status": "accepted",
        }
        assert await call_status(db, call) == "on_hold"
        paused_at = await rows(
            db, "select paused_at from public.calls where id = %s", (call.call_id,)
        )
        assert paused_at[0][0] is not None
        events = await event_rows(db, call)
        assert events == [
            ("command.applied", {"command": "pause"}),
            ("status.changed", {"status": "on_hold"}),
        ]
        rows_ = await control(db, call)
        kinds = [r["kind"] for r in rows_]
        assert kinds[:2] == ["hold", "mute"]
        assert kinds[2:] == ["reassure"] * 7  # every 15 s, before the 2 min cap
        assert rows_[0]["payload"]["message"] == {"type": "say", "content": "One moment please."}
        assert 3 < rows_[1]["due_in_s"] <= 4
        assert [m["type"] for m in rows_[2]["payload"]["messages"]] == ["control", "say", "control"]

    asyncio.run(scenario())


def test_pause_freezes_the_open_ping_and_resume_restarts_it(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call)[1])
            async with await psycopg.AsyncConnection.connect(db, autocommit=True) as conn:
                await conn.execute(
                    "update public.pings set expires_at = clock_timestamp() + interval '20 seconds'"
                    " where call_id = %s",
                    (call.call_id,),
                )
            await command(client, call, "pause")
            [ping] = await ping_rows(db, call)
            assert ping["expires_at"] is None
            assert 18_000 < ping["remaining_ms"] <= 20_000
            frozen = ping["remaining_ms"]
            # A frozen ping never times out.
            await expire_due_pings(app.state.pool)
            assert (await ping_rows(db, call))[0]["state"] == "open"
            assert {o["state"] for o in await control(db, call) if o["kind"] == "filler"} == {
                "cancelled"
            }

            await command(client, call, "resume")
        [ping] = await ping_rows(db, call)
        assert ping["state"] == "open" and ping["expires_at"] is not None
        left = await rows(
            db,
            "select extract(epoch from (expires_at - clock_timestamp())) * 1000"
            " from public.pings where ping_id = %s",
            (ping["ping_id"],),
        )
        assert frozen - 2_000 < float(left[0][0]) <= frozen
        assert await call_status(db, call) == "waiting_on_you"
        opened = [p for t, p in await event_rows(db, call) if t == "ping.opened"]
        assert [p["expires_at"] is None for p in opened] == [False, True, False]
        # Resume: pause lines dropped, unmute queued, one filler for the ~20 s left.
        rows_ = await control(db, call)
        assert {r["state"] for r in rows_ if r["kind"] in ("hold", "mute", "reassure")} == {
            "cancelled"
        }
        pending = [r["kind"] for r in rows_ if r["state"] == "pending"]
        assert pending == ["unmute", "filler"]

    asyncio.run(scenario())


def test_resume_without_a_ping_goes_back_to_talking(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await command(client, call, "pause")
            await command(client, call, "resume")
        assert await call_status(db, call) == "talking"
        types = [p.get("command") or p.get("status") for _, p in await event_rows(db, call)]
        assert types == ["pause", "on_hold", "resume", "talking"]
        paused_at = await rows(
            db, "select paused_at from public.calls where id = %s", (call.call_id,)
        )
        assert paused_at[0][0] is None

    asyncio.run(scenario())


def test_pause_and_resume_twice_change_nothing(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            assert (await command(client, call, "resume")).status_code == 200  # not paused
            await command(client, call, "pause")
            count = len(await event_rows(db, call))
            assert (await command(client, call, "pause")).status_code == 200
            assert len(await event_rows(db, call)) == count

    asyncio.run(scenario())


def test_cannot_pause_before_they_pick_up(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db, status="dialing")

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            response = await command(client, call, "pause")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "invalid_call_state"
        assert await call_status(db, call) == "dialing"

    asyncio.run(scenario())


def test_a_reply_while_paused_waits_for_resume(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    voice = FakeVoicePlatform()

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call, question="Thu 4pm?")[1])
            await command(client, call, "pause")
            [ping] = await ping_rows(db, call)
            reply = await client.post(
                f"/calls/{call.call_id}/pings/{ping['ping_id']}/reply",
                json={"choice": 0},
                headers=auth_headers(call.user_id),
            )
            assert reply.status_code == 200
            await send_due_controls(app.state.pool, voice)
            # Only the hold line went out; the answer waits while paused.
            assert [m["type"] for m in sent_to(voice, call)] == ["say"]
            assert await call_status(db, call) == "on_hold"

            await command(client, call, "resume")
            await send_due_controls(app.state.pool, voice)
        sent = sent_to(voice, call)
        assert sent[1] == {"type": "control", "control": "unmute-assistant"}
        assert sent[2]["type"] == "add-message"
        assert sent[2]["message"]["content"] == 'Alex\'s answer to "Thu 4pm?": Ask for Fri'
        assert len(sent) == 3

    asyncio.run(scenario())


def test_end_says_goodbye_and_hangs_up_later(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    voice = FakeVoicePlatform()

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call)[1])
            response = await command(client, call, "end")
            assert response.status_code == 200
            [ping] = await ping_rows(db, call)
            assert ping["state"] == "cancelled"
            # The ping's fillers are cancelled; goodbye now, hang-up 6 s later.
            await send_due_controls(app.state.pool, voice)
            assert sent_to(voice, call) == [
                {
                    "type": "say",
                    "content": "I'm sorry, I need to go now. Alex will follow up. "
                    "Thank you for your time. Goodbye.",
                    "endCallAfterSpoken": True,
                }
            ]
            hangup = [r for r in await control(db, call) if r["kind"] == "hangup"]
            assert 5 < hangup[0]["due_in_s"] <= 6
            await make_due(db, call)
            await send_due_controls(app.state.pool, voice)
        assert sent_to(voice, call)[-1] == {"type": "end-call"}
        events = await event_rows(db, call)
        assert ("command.applied", {"command": "end"}) in events
        assert ("ping.resolved", {"ping_id": str(ping["ping_id"]), "state": "cancelled"}) in events

    asyncio.run(scenario())


def test_end_while_paused_unmutes_before_the_goodbye(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await command(client, call, "pause")
            await command(client, call, "end")
        rows_ = await control(db, call)
        assert {r["state"] for r in rows_ if r["kind"] in ("hold", "mute", "reassure")} == {
            "cancelled"
        }
        [goodbye] = [r for r in rows_ if r["kind"] == "goodbye"]
        types = [m.get("control") or m["type"] for m in goodbye["payload"]["messages"]]
        assert types == ["unmute-assistant", "say"]

    asyncio.run(scenario())


def test_after_end_nothing_new_starts(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await command(client, call, "end")
            again = await command(client, call, "end")
            pause = await command(client, call, "pause")
            ask = await post(client, ask_body(call)[1])
        assert again.status_code == 200
        assert pause.status_code == 409
        assert "ended" in ask.json()["results"][0]["result"]
        assert await ping_rows(db, call) == []
        assert [r["kind"] for r in await control(db, call)].count("goodbye") == 1

    asyncio.run(scenario())


def test_pause_cap_offers_a_call_back_and_ends(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call)[1])
            await command(client, call, "pause")
            await set_paused_at(db, call, 119)
            await end_overdue_pauses(app.state.pool)
            assert not [r for r in await control(db, call) if r["kind"] == "goodbye"]
            await set_paused_at(db, call, 121)
            await end_overdue_pauses(app.state.pool)
            await end_overdue_pauses(app.state.pool)  # only once
        rows_ = await control(db, call)
        [goodbye] = [r for r in rows_ if r["kind"] == "goodbye"]
        say = goodbye["payload"]["messages"][-1]
        assert "will call you back" in say["content"] and say["endCallAfterSpoken"] is True
        assert [p["state"] for p in await ping_rows(db, call)] == ["cancelled"]
        outcome = await rows(db, "select outcome from public.calls where id = %s", (call.call_id,))
        assert outcome[0][0] == "needs_follow_up"
        decisions = [p for t, p in await event_rows(db, call) if t == "agent.decision"]
        assert decisions == [
            {
                "situation": "The call was paused for 2 minutes",
                "action": "end_call",
                "reason": "Offered a call-back so the business wasn't left waiting.",
            }
        ]

    asyncio.run(scenario())


def test_instruction_is_not_handled_here(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            response = await client.post(
                f"/calls/{call.call_id}/commands",
                json={"type": "instruction", "text": "ask about Saturday"},
                headers=auth_headers(call.user_id),
            )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unsupported_command"

    asyncio.run(scenario())


def test_commands_need_the_owner_auth_and_a_key(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    stranger = create_fresh_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            url = f"/calls/{call.call_id}/commands"
            not_mine = await client.post(
                url, json={"type": "pause"}, headers=auth_headers(stranger.user_id)
            )
            missing = await client.post(
                f"/calls/{uuid.uuid4()}/commands",
                json={"type": "pause"},
                headers=auth_headers(call.user_id),
            )
            no_auth = await client.post(url, json={"type": "pause"})
            bad = await client.post(url, json={"type": "hold"}, headers=auth_headers(call.user_id))
        assert not_mine.status_code == 404 and missing.status_code == 404
        assert no_auth.status_code == 401 and bad.status_code == 422
        assert await call_status(db, call) == "talking"

    asyncio.run(scenario())


def test_same_key_replays_the_command(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            headers = auth_headers(call.user_id)
            url = f"/calls/{call.call_id}/commands"
            first = await client.post(url, json={"type": "pause"}, headers=headers)
            again = await client.post(url, json={"type": "pause"}, headers=headers)
        assert first.json() == again.json()
        assert [t for t, _ in await event_rows(db, call)].count("command.applied") == 1

    asyncio.run(scenario())


def test_a_timeout_reached_before_pause_is_held_until_resume(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    voice = FakeVoicePlatform()

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call)[1])
            await expire_now(db, call)
            await expire_due_pings(app.state.pool)
            await command(client, call, "pause")
            await send_due_controls(app.state.pool, voice)
            assert "add-message" not in [m["type"] for m in sent_to(voice, call)]
            await command(client, call, "resume")
            await send_due_controls(app.state.pool, voice)
        assert sent_to(voice, call)[-1]["type"] == "add-message"

    asyncio.run(scenario())
