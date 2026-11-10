"""POST /calls/{call_id}/pings/{ping_id}/reply: the reply vs timeout race (DC-6, DC-8)."""

import asyncio
import json
import uuid

import pytest

from app.calls.sweeper import expire_due_pings
from tests.calls_helpers import (
    FAKE_DOB,
    ask_body,
    auth_headers,
    call_status,
    event_rows,
    expire_now,
    make_app,
    outbox_rows,
    ping_rows,
    seed_call,
)
from tests.conftest import SupabaseEnv, create_fresh_call
from tests.test_webhooks import post, rows, serve

pytestmark = pytest.mark.db


def _url(call_id: object, ping_id: object) -> str:
    return f"/calls/{call_id}/pings/{ping_id}/reply"


def test_choice_reply_answers_and_queues_it_for_the_agent(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await post(client, ask_body(call, question="They offered Thu 4pm")[1])
            [ping] = await ping_rows(db, call)
            response = await client.post(
                _url(call.call_id, ping["ping_id"]),
                json={"choice": 0},
                headers=auth_headers(call.user_id),
            )
        assert response.status_code == 200
        assert response.json() == {
            "ping_id": str(ping["ping_id"]),
            "state": "answered",
            "reply_text": "Ask for Fri",
        }
        [ping] = await ping_rows(db, call)
        assert ping["state"] == "answered"
        assert await call_status(db, call) == "talking"
        events = await event_rows(db, call)
        assert (
            "ping.resolved",
            {"ping_id": str(ping["ping_id"]), "state": "answered", "reply_text": "Ask for Fri"},
        ) in events
        outbox = await outbox_rows(db, call)
        [reply] = [o for o in outbox if o["kind"] == "reply"]
        assert reply["payload"]["message"] == {
            "type": "add-message",
            "message": {
                "role": "system",
                "content": 'Alex\'s answer to "They offered Thu 4pm": Ask for Fri',
            },
            "triggerResponseEnabled": True,
        }
        assert {o["state"] for o in outbox if o["kind"] == "filler"} == {"cancelled"}

    asyncio.run(scenario())


def test_free_text_is_cleaned_before_it_reaches_the_agent(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await post(client, ask_body(call, question="Thu 4pm?")[1])
            [ping] = await ping_rows(db, call)
            response = await client.post(
                _url(call.call_id, ping["ping_id"]),
                json={"text": "  ask\tfor\n Saturday\u0007 "},
                headers=auth_headers(call.user_id),
            )
        assert response.json()["reply_text"] == "ask for Saturday"
        [reply] = [o for o in await outbox_rows(db, call) if o["kind"] == "reply"]
        content = reply["payload"]["message"]["message"]["content"]
        assert content == 'Alex\'s answer to "Thu 4pm?": ask for Saturday'

    asyncio.run(scenario())


def test_share_releases_the_field_value_only_to_the_agent(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await post(client, ask_body(call, field_requested="date_of_birth")[1])
            [ping] = await ping_rows(db, call)
            response = await client.post(
                _url(call.call_id, ping["ping_id"]),
                json={"choice": 0},
                headers=auth_headers(call.user_id),
            )
        assert response.json()["reply_text"] == "Share it"
        [reply] = [o for o in await outbox_rows(db, call) if o["kind"] == "reply"]
        assert FAKE_DOB in reply["payload"]["message"]["message"]["content"]
        events = await event_rows(db, call)
        assert ("field.shared", {"field_key": "date_of_birth", "state": "approved"}) in events
        assert FAKE_DOB not in json.dumps([p for _, p in events])
        shared = await rows(
            db,
            "select field_key, state from public.shared_fields where call_id = %s",
            (call.call_id,),
        )
        assert shared == [("date_of_birth", "approved")]

    asyncio.run(scenario())


def test_dont_share_keeps_the_value_private(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await post(client, ask_body(call, field_requested="date_of_birth")[1])
            [ping] = await ping_rows(db, call)
            await client.post(
                _url(call.call_id, ping["ping_id"]),
                json={"choice": 1},
                headers=auth_headers(call.user_id),
            )
        [reply] = [o for o in await outbox_rows(db, call) if o["kind"] == "reply"]
        content = reply["payload"]["message"]["message"]["content"]
        assert FAKE_DOB not in content and "do not share" in content
        assert [t for t, _ in await event_rows(db, call)].count("field.shared") == 0

    asyncio.run(scenario())


def test_free_text_never_releases_an_ask_me_first_field(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await post(client, ask_body(call, field_requested="date_of_birth")[1])
            [ping] = await ping_rows(db, call)
            response = await client.post(
                _url(call.call_id, ping["ping_id"]),
                json={"text": "sure, share it"},
                headers=auth_headers(call.user_id),
            )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_reply"
        [ping] = await ping_rows(db, call)
        assert ping["state"] == "open"

    asyncio.run(scenario())


def test_a_reply_after_the_timeout_won_gets_409(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call)[1])
            [ping] = await ping_rows(db, call)
            await expire_now(db, call)
            # Past the deadline, the reply loses even before the sweeper has run.
            late = await client.post(
                _url(call.call_id, ping["ping_id"]),
                json={"choice": 0},
                headers=auth_headers(call.user_id),
            )
            assert late.status_code == 409 and late.json()["error"]["code"] == "ping_closed"
            assert await expire_due_pings(app.state.pool) >= 1
        [ping] = await ping_rows(db, call)
        assert ping["state"] == "expired"
        kinds = [o["kind"] for o in await outbox_rows(db, call)]
        assert "reply" not in kinds and kinds.count("timeout") == 1

    asyncio.run(scenario())


def test_reply_and_timeout_race_has_exactly_one_winner(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call)[1])
            [ping] = await ping_rows(db, call)
            await expire_now(db, call)
            reply, expired = await asyncio.gather(
                client.post(
                    _url(call.call_id, ping["ping_id"]),
                    json={"choice": 0},
                    headers=auth_headers(call.user_id),
                ),
                expire_due_pings(app.state.pool),
            )
        kinds = [o["kind"] for o in await outbox_rows(db, call) if o["lane"] == "control"]
        assert kinds.count("reply") + kinds.count("timeout") == 1
        assert reply.status_code == 409

    asyncio.run(scenario())


def test_a_replaced_question_cannot_be_answered(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await post(client, ask_body(call, question="Thu?", at_ms=1794081610000)[1])
            await post(client, ask_body(call, question="Fri?", at_ms=1794081620000)[1])
            old, _ = await ping_rows(db, call)
            response = await client.post(
                _url(call.call_id, old["ping_id"]),
                json={"choice": 0},
                headers=auth_headers(call.user_id),
            )
        assert response.status_code == 409

    asyncio.run(scenario())


def test_a_paused_ping_can_still_be_answered(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db, status="on_hold")

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await post(client, ask_body(call)[1])
            [ping] = await ping_rows(db, call)
            response = await client.post(
                _url(call.call_id, ping["ping_id"]),
                json={"choice": 1},
                headers=auth_headers(call.user_id),
            )
        assert response.status_code == 200
        assert await call_status(db, call) == "on_hold"  # still paused until Resume

    asyncio.run(scenario())


def test_same_idempotency_key_replays_the_first_answer(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await post(client, ask_body(call)[1])
            [ping] = await ping_rows(db, call)
            headers = auth_headers(call.user_id)
            first = await client.post(
                _url(call.call_id, ping["ping_id"]), json={"choice": 0}, headers=headers
            )
            again = await client.post(
                _url(call.call_id, ping["ping_id"]), json={"choice": 2}, headers=headers
            )
        assert again.status_code == 200 and again.json() == first.json()
        assert [o["kind"] for o in await outbox_rows(db, call)].count("reply") == 1

    asyncio.run(scenario())


def test_choice_out_of_range_is_rejected(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await post(client, ask_body(call, replies=["Yes", "No"])[1])
            [ping] = await ping_rows(db, call)
            response = await client.post(
                _url(call.call_id, ping["ping_id"]),
                json={"choice": 3},
                headers=auth_headers(call.user_id),
            )
        assert response.status_code == 422
        [ping] = await ping_rows(db, call)
        assert ping["state"] == "open"

    asyncio.run(scenario())


def test_someone_elses_call_is_not_found(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    stranger = create_fresh_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await post(client, ask_body(call)[1])
            [ping] = await ping_rows(db, call)
            response = await client.post(
                _url(call.call_id, ping["ping_id"]),
                json={"choice": 0},
                headers=auth_headers(stranger.user_id),
            )
            missing = await client.post(
                _url(call.call_id, uuid.uuid4()),
                json={"choice": 0},
                headers=auth_headers(call.user_id),
            )
        assert response.status_code == 404 and missing.status_code == 404
        [ping] = await ping_rows(db, call)
        assert ping["state"] == "open"

    asyncio.run(scenario())


def test_reply_needs_auth_and_an_idempotency_key(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            url = _url(call.call_id, uuid.uuid4())
            no_auth = await client.post(url, json={"choice": 0})
            headers = auth_headers(call.user_id)
            del headers["Idempotency-Key"]
            no_key = await client.post(url, json={"choice": 0}, headers=headers)
            bad_body = await client.post(
                url, json={"choice": 0, "text": "x"}, headers=auth_headers(call.user_id)
            )
        assert no_auth.status_code == 401
        assert no_key.status_code == 400
        assert bad_body.status_code == 422

    asyncio.run(scenario())
