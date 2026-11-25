"""GET /calls and GET /calls/{call_id}/result (app/postcall/history.py)."""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app.contracts.generated import ReportOutcomeArgs
from app.main import create_app
from app.postcall.history import claims_accepted, parse_started
from app.postcall.jobs import run_due_jobs
from tests.calls_helpers import auth_headers, make_app, seed_call
from tests.conftest import SupabaseEnv
from tests.test_webhooks import (
    fixture,
    make_settings,
    post,
    rows,
    serve,
    tool_body,
    transcript_body,
)

BOOKED_ARGS = {
    "outcome": "booked",
    "business_confirmed": True,
    "details": {"date": "2026-10-24", "time": "19:30", "staff_name": "Marco"},
    "open_items": ["Bring the confirmation number"],
}


def test_history_needs_a_signed_in_user() -> None:
    with TestClient(create_app(make_settings())) as client:
        assert client.get("/calls").status_code == 401
        assert client.get(f"/calls/{uuid.uuid4()}/result").status_code == 401


def test_unreadable_start_time_is_dropped_not_a_500() -> None:
    assert parse_started("2026-11-09T18:00:00+00:00") is not None
    assert parse_started("not a time") is None
    assert parse_started(None) is None
    assert parse_started(1794081601000) is None


def test_claims_show_only_for_the_report_the_outcome_job_accepted() -> None:
    booked = ReportOutcomeArgs.model_validate(BOOKED_ARGS)
    assert claims_accepted(booked, "booked")
    assert not claims_accepted(booked, "needs_follow_up")  # downgraded
    assert not claims_accepted(None, "booked")
    # A later report with another outcome was never checked by the outcome job.
    cancelled = ReportOutcomeArgs.model_validate({**BOOKED_ARGS, "outcome": "cancelled"})
    assert not claims_accepted(cancelled, "booked")


def test_loose_receipts_still_read_with_their_details() -> None:
    """#55 dropped the schema patterns, so receipts may hold "7:30 PM" or "ABCD"."""
    loose = {**BOOKED_ARGS, "details": {"time": "7:30 PM", "confirmation_number": "ABCD"}}
    report = ReportOutcomeArgs.model_validate(loose)
    assert report.details is not None
    assert (report.details.time, report.details.confirmation_number) == ("7:30 PM", "ABCD")


@pytest.mark.db
def test_list_shows_only_the_callers_calls_newest_first(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    older, newer, someone_else = seed_call(db), seed_call(db), seed_call(db)

    async def scenario() -> None:
        # Give the user two calls: move `newer` (and its brief, which must belong to the
        # same user to be shown) to the same user as `older`, one hour later.
        await rows(
            db,
            "update public.briefs set user_id = %s"
            " where id = (select brief_id from public.calls where id = %s) returning id",
            (older.user_id, newer.call_id),
        )
        await rows(
            db,
            "update public.calls set user_id = %s, created_at = now() + interval '1 hour'"
            " where id = %s returning id",
            (older.user_id, newer.call_id),
        )
        async with serve(make_app(db)) as client:
            response = await client.get("/calls", headers=auth_headers(older.user_id))
        assert response.status_code == 200
        calls = response.json()["calls"]
        assert [c["id"] for c in calls] == [str(newer.call_id), str(older.call_id)]
        assert str(someone_else.call_id) not in {c["id"] for c in calls}
        assert calls[0]["business_name"] == "Alex Chen Bistro"
        assert calls[0]["goal"] == "Book a table for two"
        assert calls[0]["outcome"] is None

    asyncio.run(scenario())


@pytest.mark.db
def test_result_has_outcome_details_decisions_shared_and_transcript(
    supabase_env: SupabaseEnv,
) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(
                client,
                transcript_body(
                    call,
                    "Hi, I'm calling on behalf of Alex. A table for 2 Saturday?",
                    role="assistant",
                    timestamp=1794081601000,
                ),
            )
            await post(
                client,
                transcript_body(
                    call,
                    "Got it, 7:30 on October 24th. I'm Marco.",
                    role="user",
                    timestamp=1794081609000,
                ),
            )
            tc = f"tc-{uuid.uuid4()}"
            decision = {
                "situation": "7pm is full",
                "action": "take_fallback",
                "confidence": "high",
                "reason": "Matched your fallback",
                "dimensions": {
                    "needs_ask_first_field": False,
                    "involves_money": False,
                    "meets_constraints": True,
                    "matches_fallback": True,
                    "answerable_from_brief": True,
                    "understood": True,
                },
            }
            await post(
                client,
                tool_body(
                    call,
                    [(f"d-{tc}", "report_decision", decision), (tc, "report_outcome", BOOKED_ARGS)],
                ),
            )
            await post(client, fixture("end-of-call-report", call))
            await run_due_jobs(app.state.pool)
            response = await client.get(
                f"/calls/{call.call_id}/result", headers=auth_headers(call.user_id)
            )

        assert response.status_code == 200
        result = response.json()
        assert result["call"]["outcome"] == "booked"
        assert result["business_confirmed"] is True
        assert result["details"]["time"] == "19:30"
        assert result["details"]["staff_name"] == "Marco"
        assert result["open_items"] == ["Bring the confirmation number"]
        assert [(d["action"], d["reason"]) for d in result["decisions"]] == [
            ("take_fallback", "Matched your fallback")
        ]
        assert "confidence" not in result["decisions"][0]  # never shown (DC-5)
        assert result["shared_fields"] == [{"field_key": "calling_name", "state": "spoken"}]
        assert [(line["role"], line["text"]) for line in result["transcript"]] == [
            ("agent", "Hi, I'm calling on behalf of Alex. A table for 2 Saturday?"),
            ("business", "Got it, 7:30 on October 24th. I'm Marco."),
        ]

    asyncio.run(scenario())


@pytest.mark.db
def test_result_of_someone_elses_call_is_not_found(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    mine, theirs = seed_call(db), seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            other = await client.get(
                f"/calls/{theirs.call_id}/result", headers=auth_headers(mine.user_id)
            )
            missing = await client.get(
                f"/calls/{uuid.uuid4()}/result", headers=auth_headers(mine.user_id)
            )
        assert other.status_code == 404
        assert missing.status_code == 404

    asyncio.run(scenario())


@pytest.mark.db
def test_result_before_any_report_has_empty_parts(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            response = await client.get(
                f"/calls/{call.call_id}/result", headers=auth_headers(call.user_id)
            )
        result = response.json()
        assert response.status_code == 200
        assert result["call"]["outcome"] is None
        assert result["business_confirmed"] is False
        assert result["open_items"] == []
        assert result["result_items"] == [] and result["follow_up"] is None
        assert result["decisions"] == [] and result["transcript"] == []

    asyncio.run(scenario())


@pytest.mark.db
def test_transcript_is_in_spoken_order_even_when_lines_arrive_out_of_order(
    supabase_env: SupabaseEnv,
) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            # The business's reply (said second) arrives before the agent's question.
            await post(
                client,
                transcript_body(
                    call, "Yes, we have a table.", role="user", timestamp=1794081609000
                ),
            )
            await post(
                client,
                transcript_body(
                    call, "Do you have a table for 2?", role="assistant", timestamp=1794081601000
                ),
            )
            response = await client.get(
                f"/calls/{call.call_id}/result", headers=auth_headers(call.user_id)
            )
        lines = [(line["role"], line["text"]) for line in response.json()["transcript"]]
        assert lines == [
            ("agent", "Do you have a table for 2?"),
            ("business", "Yes, we have a table."),
        ]

    asyncio.run(scenario())


@pytest.mark.db
def test_result_shows_only_the_details_the_business_said(supabase_env: SupabaseEnv) -> None:
    """The date and time back the booking, but the agent's staff name and confirmation number
    were never said: the result page must not show them as agreed (PO-2)."""
    db = supabase_env.db_url
    call = seed_call(db)
    invented = {
        **BOOKED_ARGS,
        "details": {
            "date": "2026-10-24",
            "time": "19:30",
            "staff_name": "Marco",
            "confirmation_number": "4417",
        },
    }

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(
                client,
                transcript_body(
                    call,
                    "You're all set for 7:30 on October 24th.",
                    role="user",
                    timestamp=1794081609000,
                ),
            )
            tc = f"tc-{uuid.uuid4()}"
            await post(client, tool_body(call, [(tc, "report_outcome", invented)]))
            await post(client, fixture("end-of-call-report", call))
            await run_due_jobs(app.state.pool)
            response = await client.get(
                f"/calls/{call.call_id}/result", headers=auth_headers(call.user_id)
            )
        result = response.json()
        assert result["call"]["outcome"] == "booked"
        assert result["details"] == {
            "date": "2026-10-24",
            "time": "19:30",
            "confirmation_number": None,
            "staff_name": None,
        }

    asyncio.run(scenario())


@pytest.mark.db
def test_result_items_are_checked_against_the_transcript(supabase_env: SupabaseEnv) -> None:
    """A row the business's words back is shown with its line numbers, an unbacked change is
    left out, and an unbacked as_asked row is shown marked (docs/call-result-items.md)."""
    db = supabase_env.db_url
    call = seed_call(db)
    args = {
        **BOOKED_ARGS,
        "follow_up": "Bring the confirmation number.",
        "result_items": [
            {
                "ref": {"kind": "extra"},
                "label": "Time",
                "got": "7:30 PM on Oct 24",
                "status": "changed",
                "business_said": "all set for 7:30 on October 24th",
            },
            {
                "ref": {"kind": "extra"},
                "label": "Parking",
                "got": "Free parking",
                "status": "changed",
                "business_said": "Parking is free",  # never said
            },
            {
                "ref": {"kind": "extra"},
                "label": "Party",
                "got": "Table for 2",
                "status": "as_asked",
            },
        ],
    }

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(
                client,
                transcript_body(
                    call,
                    "You're all set for 7:30 on October 24th.",
                    role="user",
                    timestamp=1794081609000,
                ),
            )
            tc = f"tc-{uuid.uuid4()}"
            await post(client, tool_body(call, [(tc, "report_outcome", args)]))
            await post(client, fixture("end-of-call-report", call))
            await run_due_jobs(app.state.pool)
            response = await client.get(
                f"/calls/{call.call_id}/result", headers=auth_headers(call.user_id)
            )
        result = response.json()
        assert result["follow_up"] == "Bring the confirmation number."
        assert [(r["label"], r["verified"]) for r in result["result_items"]] == [
            ("Time", True),
            ("Party", False),
        ]
        time_row = result["result_items"][0]
        assert time_row["evidence"] == [result["transcript"][0]["seq"]]
        assert time_row["asked"] is None  # an extra row has no brief item

    asyncio.run(scenario())


@pytest.mark.db
def test_downgraded_report_does_not_show_as_a_confirmed_booking(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            # The agent claims a confirmed booking, but the business never said so.
            await post(
                client,
                transcript_body(
                    call, "Let me check and call you back.", role="user", timestamp=1794081609000
                ),
            )
            tc = f"tc-{uuid.uuid4()}"
            await post(client, tool_body(call, [(tc, "report_outcome", BOOKED_ARGS)]))
            await post(client, fixture("end-of-call-report", call))
            await run_due_jobs(app.state.pool)
            response = await client.get(
                f"/calls/{call.call_id}/result", headers=auth_headers(call.user_id)
            )
        result = response.json()
        assert result["call"]["outcome"] == "needs_follow_up"
        assert result["business_confirmed"] is False
        assert result["details"] == {
            "date": None,
            "time": None,
            "confirmation_number": None,
            "staff_name": None,
        }
        assert result["open_items"] == ["Bring the confirmation number"]

    asyncio.run(scenario())


@pytest.mark.db
def test_malformed_call_id_is_422(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            response = await client.get(
                "/calls/not-a-uuid/result", headers=auth_headers(call.user_id)
            )
        assert response.status_code == 422

    asyncio.run(scenario())


@pytest.mark.db
def test_brief_of_another_user_is_not_shown(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    mine, theirs = seed_call(db), seed_call(db)

    async def scenario() -> None:
        # A call pointing at someone else's brief must not leak that brief's details.
        await rows(
            db,
            "update public.calls set brief_id = (select brief_id from public.calls where id = %s)"
            " where id = %s returning id",
            (theirs.call_id, mine.call_id),
        )
        async with serve(make_app(db)) as client:
            listed = await client.get("/calls", headers=auth_headers(mine.user_id))
        call = next(c for c in listed.json()["calls"] if c["id"] == str(mine.call_id))
        assert call["business_name"] == "" and call["goal"] == ""

    asyncio.run(scenario())
