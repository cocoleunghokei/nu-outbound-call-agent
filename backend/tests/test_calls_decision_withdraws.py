"""A better offer taken during a ping closes the open question (Q2, DC-4, DC-7). Fake data.

The business improves its offer while the user is deciding, and the agent takes it on its own
with ``report_decision``. The open ``decision`` ping must close, so the user never answers a
question that no longer applies, and its "still checking" lines must stop.
"""

import asyncio
import uuid
from typing import Any

import pytest

from app.calls import messages
from tests.calls_helpers import (
    ask_body,
    call_status,
    event_rows,
    make_app,
    outbox_rows,
    ping_rows,
    seed_call,
)
from tests.conftest import FreshCall, SupabaseEnv
from tests.test_webhooks import post, serve, tool_body

pytestmark = pytest.mark.db

ASKED_AT_MS = 1794081610000


def decision_body(
    call: FreshCall, action: str, *, at_ms: int, field_key: str = "phone"
) -> tuple[str, dict[str, Any]]:
    tc_id = f"tc-{uuid.uuid4()}"
    args: dict[str, Any] = {
        "situation": "They now also offer Fri 10am",
        "action": action,
        "reason": "Matched your fallback: Fri 10am.",
        "confidence": "high",
        "dimensions": {
            "needs_ask_first_field": False,
            "involves_money": False,
            "meets_constraints": False,
            "matches_fallback": True,
            "answerable_from_brief": True,
            "understood": True,
        },
    }
    if action == "share_field":
        args["field_key"] = field_key
    return tc_id, tool_body(call, [(tc_id, "report_decision", args)], timestamp=at_ms)


async def ask_then_decide(
    db: str, call: FreshCall, action: str, *, decided_at_ms: int, field: str | None = None
) -> str:
    """Open a ping, then report a decision. Returns the decision's tool result."""
    async with serve(make_app(db)) as client:
        _, ask = ask_body(call, at_ms=ASKED_AT_MS, field_requested=field)
        await post(client, ask)
        tc_id, body = decision_body(call, action, at_ms=decided_at_ms)
        response = await post(client, body)
    [result] = response.json()["results"]
    assert result["toolCallId"] == tc_id
    return str(result["result"])


@pytest.mark.parametrize("action", ["accept", "take_fallback"])
def test_taking_an_offer_withdraws_the_open_question(
    supabase_env: SupabaseEnv, action: str
) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        result = await ask_then_decide(db, call, action, decided_at_ms=ASKED_AT_MS + 5_000)
        assert result == messages.question_withdrawn_result()
        [ping] = await ping_rows(db, call)
        assert ping["state"] == "superseded"
        assert await call_status(db, call) == "talking"
        fillers = [o for o in await outbox_rows(db, call) if o["kind"] == "filler"]
        assert fillers and all(o["state"] == "cancelled" for o in fillers)
        resolved = [p for t, p in await event_rows(db, call) if t == "ping.resolved"]
        assert resolved == [{"ping_id": str(ping["ping_id"]), "state": "superseded"}]

    asyncio.run(scenario())


def test_sharing_a_field_during_a_ping_leaves_the_question_open(
    supabase_env: SupabaseEnv,
) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        result = await ask_then_decide(db, call, "share_field", decided_at_ms=ASKED_AT_MS + 5_000)
        assert result == "Noted."
        [ping] = await ping_rows(db, call)
        assert ping["state"] == "open"
        assert await call_status(db, call) == "waiting_on_you"

    asyncio.run(scenario())


def test_an_ask_me_first_approval_is_never_withdrawn_by_a_decision(
    supabase_env: SupabaseEnv,
) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        result = await ask_then_decide(
            db, call, "accept", decided_at_ms=ASKED_AT_MS + 5_000, field="date_of_birth"
        )
        assert result == "Noted."
        [ping] = await ping_rows(db, call)
        assert (ping["kind"], ping["state"]) == ("field_approval", "open")

    asyncio.run(scenario())


def test_a_decision_older_than_the_question_withdraws_nothing(
    supabase_env: SupabaseEnv,
) -> None:
    """A late webhook: the decision was made before the question was asked."""
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        result = await ask_then_decide(db, call, "accept", decided_at_ms=ASKED_AT_MS - 5_000)
        assert result == "Noted."
        [ping] = await ping_rows(db, call)
        assert ping["state"] == "open"

    asyncio.run(scenario())


def test_a_decision_with_no_open_question_is_just_logged(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            _, body = decision_body(call, "accept", at_ms=ASKED_AT_MS)
            response = await post(client, body)
        assert response.json()["results"][0]["result"] == "Noted."
        assert await ping_rows(db, call) == []
        assert await call_status(db, call) == "talking"

    asyncio.run(scenario())


def test_a_field_shared_again_is_logged_once(supabase_env: SupabaseEnv) -> None:
    """The business couldn't hear the phone number and the agent said it again: one row."""
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            for at_ms, field in (
                (ASKED_AT_MS, "phone"),
                (ASKED_AT_MS + 5_000, "phone"),
                (ASKED_AT_MS + 9_000, "email"),
            ):
                _, body = decision_body(call, "share_field", at_ms=at_ms, field_key=field)
                response = await post(client, body)
                assert response.json()["results"][0]["result"] == "Noted."
        decisions = [p for kind, p in await event_rows(db, call) if kind == "agent.decision"]
        assert [d["field_key"] for d in decisions] == ["phone", "email"]

    asyncio.run(scenario())
