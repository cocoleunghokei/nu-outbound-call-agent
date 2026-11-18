"""Deterministic gates on the agent's actions (``app/calls/gates.py``). Fake data.

Found by the in-call eval: the agent can accept a deposit nobody approved, accept an offer its
own checks say breaks the rules, or end the call as Needs follow-up without ever asking the
user (C4-14). The pure decisions are tested without a database; the wiring through the real
webhook and reply endpoints is tested against the local Supabase stack (``db``).
"""

import asyncio
import uuid
from typing import Any

import psycopg
import pytest

from app.calls.gates import (
    CARD_TEXT_MAX,
    REFUSALS,
    REFUSED_GIVE_UP,
    REFUSED_LOW_OFFER,
    REFUSED_MONEY,
    REFUSED_NEEDLESS_PING,
    REFUSED_TOO_LONG,
    REFUSED_UNSAID_NAME,
    CallFacts,
    facts_from_timeline,
    give_up_gate,
    is_approving,
    length_gate,
    low_offer_gate,
    mentions_money,
    money_gate,
    needless_ping_gate,
    unsaid_name_gate,
)
from app.postcall.jobs import latest_report
from tests.calls_helpers import (
    ask_body,
    auth_headers,
    event_rows,
    make_app,
    ping_rows,
    seed_call,
)
from tests.conftest import SupabaseEnv
from tests.test_webhooks import post, rows, serve, tool_body, transcript_body

LOW = {"meets_constraints": False, "matches_fallback": False}
FALLBACK = {"meets_constraints": False, "matches_fallback": True}
FITS = {"meets_constraints": True, "matches_fallback": False}

# --- the pure decisions --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "money"),
    [
        ("There's a $25 booking deposit.", True),
        ("We need a credit card to hold it.", True),
        ("There's a $50 no-show fee.", True),
        ("We'll put a card hold on it.", True),
        ("Hold on one sec.", False),
        ("Saturday 7pm for 6 works.", False),
        ("A 20% service charge applies to parties of 6.", True),
    ],
)
def test_mentions_money(text: str, money: bool) -> None:
    assert mentions_money(text) is money


@pytest.mark.parametrize(
    ("reply", "approves"),
    [("Yes, book it", True), ("yes", True), ("Share it", True), ("ok go ahead", True),
     ("No, ask for Friday morning", False), ("Don't share", False), ("not sure", False)],
)  # fmt: skip
def test_is_approving(reply: str, approves: bool) -> None:
    assert is_approving(reply) is approves


def test_money_needs_a_yes_after_it_was_mentioned() -> None:
    deposit = facts_from_timeline(
        [("business", "Thursday 2pm, with a $25 deposit.")], latest_ping_answer=None, ending=False
    )
    assert money_gate("accept", deposit) == REFUSED_MONEY
    assert money_gate("take_fallback", deposit) == REFUSED_MONEY
    assert money_gate("share_field", deposit) is None  # only taking an offer is gated

    approved = facts_from_timeline(
        [("business", "There's a $25 deposit."), ("answered", "Yes, that's fine")],
        latest_ping_answer="Yes, that's fine",
        ending=False,
    )
    assert money_gate("accept", approved) is None

    new_money = facts_from_timeline(
        [
            ("business", "There's a $25 deposit."),
            ("answered", "Yes"),
            ("business", "There's also a $40 card hold."),
        ],
        latest_ping_answer="Yes",
        ending=False,
    )
    assert money_gate("accept", new_money) == REFUSED_MONEY  # PI-09: a yes covers one amount


def test_a_field_approval_never_approves_money() -> None:
    facts = facts_from_timeline(
        [("business", "There's a $25 deposit."), ("field_answered", "Share it")],
        latest_ping_answer=None,
        ending=False,
    )
    assert facts.money_since_yes and facts.user_was_asked
    assert money_gate("accept", facts) == REFUSED_MONEY


def test_a_low_offer_cannot_be_accepted_without_approval() -> None:
    assert low_offer_gate("accept", LOW, CallFacts()) == REFUSED_LOW_OFFER
    assert low_offer_gate("take_fallback", LOW, CallFacts()) == REFUSED_LOW_OFFER
    assert low_offer_gate("accept", FITS, CallFacts()) is None
    assert low_offer_gate("take_fallback", FALLBACK, CallFacts()) is None
    assert low_offer_gate("accept", FALLBACK, CallFacts()) is None
    assert low_offer_gate("counter", LOW, CallFacts()) is None
    assert low_offer_gate("accept", None, CallFacts()) is None  # no scores: nothing to check
    # R-01: the user said "Yes, book it" to the latest ping, so accepting 4pm is allowed.
    assert low_offer_gate("accept", LOW, CallFacts(latest_ping_approved=True)) is None


def test_card_text_over_80_characters_is_refused() -> None:
    # Found by the eval (D5-len): reasons of 83 to 99 characters, run after run.
    fits = "x" * CARD_TEXT_MAX
    assert CARD_TEXT_MAX == 80
    assert length_gate({"reason": fits, "missed_preference": fits}) is None
    assert length_gate({"reason": f"  {fits}  "}) is None  # surrounding spaces don't count
    assert length_gate({"reason": fits + "x"}) == REFUSED_TOO_LONG
    assert length_gate({"reason": "Short.", "missed_preference": fits + "x"}) == REFUSED_TOO_LONG
    assert length_gate({}) is None
    assert length_gate({"reason": 42}) is None  # not text: the schema check rejects it
    assert REFUSED_TOO_LONG in REFUSALS  # so the eval drops the refused call from its checks


def test_pinging_about_an_offer_scored_high_is_refused() -> None:
    # Found by the eval (C4-01): "Thursday 2pm with Dr. Rivera fits your request. Confirm?"
    fits = {**FITS, "needs_ask_first_field": False, "involves_money": False}
    assert needless_ping_gate({"confidence": "high", "dimensions": fits}) == REFUSED_NEEDLESS_PING
    assert REFUSED_NEEDLESS_PING in REFUSALS
    assert needless_ping_gate({"confidence": "low", "dimensions": fits}) is None
    # Money or an ask-first field always pings, whatever the score.
    money = {**fits, "involves_money": True}
    assert needless_ping_gate({"confidence": "high", "dimensions": money}) is None
    field = {**fits, "needs_ask_first_field": True}
    assert needless_ping_gate({"confidence": "high", "dimensions": field}) is None
    assert (
        needless_ping_gate(
            {"confidence": "high", "dimensions": fits, "field_requested": "date_of_birth"}
        )
        is None
    )
    assert needless_ping_gate({"confidence": "high"}) is None  # no scores: nothing to check


def test_a_staff_name_the_business_never_said_is_refused() -> None:
    # Found by the eval (SM-06): staff_name "Luna Trattoria", the restaurant's own name.
    facts = facts_from_timeline(
        [("business", "Hi, Luna Trattoria, this is Marco Rossi speaking.")],
        latest_ping_answer=None,
        ending=False,
    )
    booked = {"outcome": "booked", "business_confirmed": True}
    assert unsaid_name_gate({**booked, "details": {"staff_name": "Marco Rossi"}}, facts) is None
    assert unsaid_name_gate({**booked, "details": {"staff_name": "marco"}}, facts) is None
    assert unsaid_name_gate({**booked, "details": {"staff_name": "Maria"}}, facts) == (
        REFUSED_UNSAID_NAME
    )
    assert unsaid_name_gate(booked, facts) is None  # no name given: nothing to check
    assert unsaid_name_gate({**booked, "details": {"staff_name": " "}}, facts) is None
    silent = facts_from_timeline([], latest_ping_answer=None, ending=False)
    assert unsaid_name_gate({**booked, "details": {"staff_name": "Luna"}}, silent) == (
        REFUSED_UNSAID_NAME
    )
    assert REFUSED_UNSAID_NAME in REFUSALS


def test_giving_up_needs_the_user_asked_first() -> None:
    assert give_up_gate("needs_follow_up", CallFacts()) == REFUSED_GIVE_UP
    assert give_up_gate("needs_follow_up", CallFacts(user_was_asked=True)) is None
    assert give_up_gate("needs_follow_up", CallFacts(ending=True)) is None  # the user pressed End
    for outcome in ("booked", "cancelled", "refused_ai", "wrong_number"):
        assert give_up_gate(outcome, CallFacts()) is None
    expired = facts_from_timeline([("expired", "")], latest_ping_answer=None, ending=False)
    assert give_up_gate("needs_follow_up", expired) is None  # T-02: the ping timed out


# --- wired into the real endpoints (needs the local Supabase stack) ------------------------

DIMS_LOW = {
    "needs_ask_first_field": False,
    "involves_money": False,
    "meets_constraints": False,
    "matches_fallback": False,
    "answerable_from_brief": True,
    "understood": True,
}


def _decision(action: str, dims: dict[str, Any]) -> dict[str, Any]:
    return {
        "situation": "They offered Thu 4pm",
        "action": action,
        "reason": "They offered Thu 4pm.",
        "confidence": "low",
        "dimensions": dims,
    }


@pytest.mark.db
def test_a_refused_accept_is_not_logged_and_a_yes_lets_it_through(
    supabase_env: SupabaseEnv,
) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await post(client, transcript_body(call, "Thursday 4pm, and there's a $25 deposit."))
            tc = f"tc-{uuid.uuid4()}"
            response = await post(
                client, tool_body(call, [(tc, "report_decision", _decision("accept", DIMS_LOW))])
            )
            assert response.json()["results"] == [{"toolCallId": tc, "result": REFUSED_MONEY}]
            assert not [p for t, p in await event_rows(db, call) if t == "agent.decision"]

            _, ask = ask_body(call, replies=["Yes, book it", "No"])
            await post(client, ask)
            [ping] = [p for p in await ping_rows(db, call) if p["state"] == "open"]
            reply = await client.post(
                f"/calls/{call.call_id}/pings/{ping['ping_id']}/reply",
                json={"choice": 0},
                headers=auth_headers(call.user_id),
            )
            assert reply.status_code == 200

            tc2 = f"tc-{uuid.uuid4()}"
            ok = await post(
                client,
                tool_body(
                    call,
                    [(tc2, "report_decision", _decision("accept", DIMS_LOW))],
                    timestamp=1794081690000,
                ),
            )
            assert ok.json()["results"] == [{"toolCallId": tc2, "result": "Noted."}]
            decisions = [p for t, p in await event_rows(db, call) if t == "agent.decision"]
            assert [d["action"] for d in decisions] == ["accept"]

    asyncio.run(scenario())


@pytest.mark.db
def test_giving_up_before_asking_is_refused_and_never_becomes_the_outcome(
    supabase_env: SupabaseEnv,
) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    give_up = {"outcome": "needs_follow_up", "business_confirmed": False}

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            tc = f"tc-{uuid.uuid4()}"
            response = await post(client, tool_body(call, [(tc, "report_outcome", give_up)]))
            assert response.json()["results"] == [{"toolCallId": tc, "result": REFUSED_GIVE_UP}]
        [(result,)] = await rows(
            db, "select result from public.tool_calls where tool_call_id = %s", (tc,)
        )
        assert result == REFUSED_GIVE_UP
        async with await psycopg.AsyncConnection.connect(db) as conn:
            assert await latest_report(conn, call.call_id) is None  # never the outcome

    asyncio.run(scenario())


@pytest.mark.db
def test_a_card_reason_over_80_characters_is_refused_and_writes_nothing(
    supabase_env: SupabaseEnv,
) -> None:
    db = supabase_env.db_url
    call = seed_call(db)
    long_reason = (
        "They offered Thursday 4pm only, and Friday morning is booked; you said before 3pm."
    )
    assert len(long_reason) > 80

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            decision = {**_decision("counter", DIMS_LOW), "reason": long_reason}
            tc = f"tc-{uuid.uuid4()}"
            response = await post(client, tool_body(call, [(tc, "report_decision", decision)]))
            assert response.json()["results"] == [{"toolCallId": tc, "result": REFUSED_TOO_LONG}]
            assert not [p for t, p in await event_rows(db, call) if t == "agent.decision"]

            ask_tc, ask = ask_body(call, reason=long_reason)
            response = await post(client, ask)
            assert response.json()["results"] == [
                {"toolCallId": ask_tc, "result": REFUSED_TOO_LONG}
            ]
            assert await ping_rows(db, call) == []  # no card reached the user

    asyncio.run(scenario())
