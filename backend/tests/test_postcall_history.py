"""GET /calls and GET /calls/{call_id}/result (app/postcall/history.py)."""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
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


@pytest.mark.db
def test_list_shows_only_the_callers_calls_newest_first(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    older, newer, someone_else = seed_call(db), seed_call(db), seed_call(db)

    async def scenario() -> None:
        # Give the user two calls: move `newer` to the same user as `older`, one hour later.
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
                    call, "Got it, 7:30 Saturday. I'm Marco.", role="user", timestamp=1794081609000
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
            ("business", "Got it, 7:30 Saturday. I'm Marco."),
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
        assert result["decisions"] == [] and result["transcript"] == []

    asyncio.run(scenario())
