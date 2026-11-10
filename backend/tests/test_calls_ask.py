"""The ask_user handler and the sweeper (DC-6, DC-7, DC-9), through the real webhook ingress."""

import asyncio
import json
import uuid

import pytest

from app.calls.messages import FIELD_APPROVAL_REPLIES
from app.calls.sweeper import expire_due_pings
from app.contracts import validator_for
from tests.calls_helpers import (
    FAKE_DOB,
    ask_body,
    call_status,
    event_rows,
    expire_now,
    make_app,
    outbox_rows,
    ping_rows,
    seed_call,
)
from tests.conftest import SupabaseEnv
from tests.test_webhooks import post, serve

pytestmark = pytest.mark.db


def test_ask_user_opens_a_ping_and_waits_on_the_user(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            _, body = ask_body(call)
            response = await post(client, body)
        result = response.json()["results"][0]["result"]
        assert "Alex" in result and "wait" in result

        [ping] = await ping_rows(db, call)
        assert ping["state"] == "open" and ping["kind"] == "decision"
        assert ping["question"] == "They offered Thu 4pm. Take it?"
        assert ping["suggested_replies"] == ["Ask for Fri", "Accept 4pm", "Decline"]
        assert ping["remaining_ms"] == 45_000 and ping["expires_at"] is not None

        assert await call_status(db, call) == "waiting_on_you"
        events = await event_rows(db, call)
        types = [t for t, _ in events]
        assert types == ["ping.opened", "status.changed"]
        opened = events[0][1]
        validator_for("ping.v1.json").validate(opened)
        assert "confidence" not in opened and "dimensions" not in opened

        outbox = await outbox_rows(db, call)
        assert [(o["lane"], o["kind"]) for o in outbox] == [
            ("notify", "ping"),
            ("control", "filler"),
            ("control", "filler"),
        ]
        assert 13 < outbox[1]["due_in_s"] <= 15 and 28 < outbox[2]["due_in_s"] <= 30
        assert outbox[1]["payload"]["message"] == {
            "type": "say",
            "content": "Thanks for holding, I'm still checking with Alex.",
        }

    asyncio.run(scenario())


def test_ask_me_first_field_gets_a_server_written_question(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            _, body = ask_body(
                call,
                question="Alex, what's your DOB? Is it 2004?",
                replies=["Yes", "No"],
                field_requested="date_of_birth",
            )
            await post(client, body)
        [ping] = await ping_rows(db, call)
        assert ping["kind"] == "field_approval" and ping["field_key"] == "date_of_birth"
        assert ping["question"] == "They're asking for your date of birth"
        assert ping["reason"] == "You set date of birth to Ask me first."
        assert ping["suggested_replies"] == list(FIELD_APPROVAL_REPLIES)
        # The private value is nowhere the browser can read.
        assert FAKE_DOB not in json.dumps([p for _, p in await event_rows(db, call)])

    asyncio.run(scenario())


def test_share_freely_field_is_an_ordinary_question(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            _, body = ask_body(call, question="Which email?", field_requested="email")
            await post(client, body)
        [ping] = await ping_rows(db, call)
        assert ping["kind"] == "decision" and ping["question"] == "Which email?"

    asyncio.run(scenario())


def test_a_newer_question_replaces_the_open_one(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await post(client, ask_body(call, question="Thu 4pm?", at_ms=1794081610000)[1])
            await post(client, ask_body(call, question="Fri 10am?", at_ms=1794081620000)[1])
        first, second = await ping_rows(db, call)
        assert (first["question"], first["state"]) == ("Thu 4pm?", "superseded")
        assert (second["question"], second["state"]) == ("Fri 10am?", "open")
        types = [t for t, _ in await event_rows(db, call)]
        assert types == ["ping.opened", "status.changed", "ping.resolved", "ping.opened"]
        # The old question's fillers are dropped; the new one gets its own.
        fillers = [o for o in await outbox_rows(db, call) if o["kind"] == "filler"]
        states = {(o["payload"]["ping_id"], o["state"]) for o in fillers}
        assert states == {(str(first["ping_id"]), "cancelled"), (str(second["ping_id"]), "pending")}

    asyncio.run(scenario())


def test_an_older_question_arriving_late_is_never_shown(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await post(client, ask_body(call, question="Fri 10am?", at_ms=1794081620000)[1])
            response = await post(
                client, ask_body(call, question="Thu 4pm?", at_ms=1794081610000)[1]
            )
        assert "newer question" in response.json()["results"][0]["result"]
        late, current = await ping_rows(db, call)
        assert (late["question"], late["state"]) == ("Thu 4pm?", "superseded")
        assert (current["question"], current["state"]) == ("Fri 10am?", "open")
        assert [t for t, _ in await event_rows(db, call)].count("ping.opened") == 1

    asyncio.run(scenario())


def test_same_timestamp_is_ordered_by_tool_call_id(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        run = uuid.uuid4().hex  # tool call ids are unique across the database
        async with serve(make_app(db)) as client:
            await post(client, ask_body(call, question="B", tool_call_id=f"tc-b-{run}")[1])
            await post(client, ask_body(call, question="A", tool_call_id=f"tc-a-{run}")[1])
        states = {p["question"]: p["state"] for p in await ping_rows(db, call)}
        assert states == {"A": "superseded", "B": "open"}

    asyncio.run(scenario())


def test_redelivered_ask_user_opens_one_ping(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        _, body = ask_body(call)
        async with serve(make_app(db)) as client:
            first = await post(client, body)
            second = await post(client, body)
        assert first.json() == second.json()
        assert len(await ping_rows(db, call)) == 1

    asyncio.run(scenario())


def test_no_ping_once_the_call_has_ended(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db, status="ended")

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            response = await post(client, ask_body(call)[1])
        assert "ended" in response.json()["results"][0]["result"]
        assert await ping_rows(db, call) == []

    asyncio.run(scenario())


def test_a_question_while_paused_starts_with_a_frozen_clock(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db, status="on_hold")

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await post(client, ask_body(call)[1])
        [ping] = await ping_rows(db, call)
        assert ping["state"] == "open"
        assert ping["expires_at"] is None and ping["remaining_ms"] == 45_000
        assert await call_status(db, call) == "on_hold"
        assert [o["kind"] for o in await outbox_rows(db, call)] == ["ping"]  # no fillers

    asyncio.run(scenario())


def test_sweeper_expires_an_unanswered_ping(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call)[1])
            await expire_due_pings(app.state.pool)
            [ping] = await ping_rows(db, call)
            assert ping["state"] == "open"  # not due yet
            await expire_now(db, call)
            await expire_due_pings(app.state.pool)
            await expire_due_pings(app.state.pool)  # a second pass changes nothing
        [ping] = await ping_rows(db, call)
        assert ping["state"] == "expired"
        assert await call_status(db, call) == "talking"
        events = await event_rows(db, call)
        assert events[-2] == (
            "ping.resolved",
            {"ping_id": str(ping["ping_id"]), "state": "expired"},
        )
        assert events[-1] == ("status.changed", {"status": "talking"})
        outbox = await outbox_rows(db, call)
        timeout = [o for o in outbox if o["kind"] == "timeout"]
        assert len(timeout) == 1
        assert "did not reply in time" in timeout[0]["payload"]["message"]["message"]["content"]
        assert {o["state"] for o in outbox if o["kind"] == "filler"} == {"cancelled"}

    asyncio.run(scenario())


def test_sweeper_never_expires_a_paused_ping(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db, status="on_hold")

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, ask_body(call)[1])
            await expire_due_pings(app.state.pool)
        [ping] = await ping_rows(db, call)
        assert ping["state"] == "open"

    asyncio.run(scenario())
