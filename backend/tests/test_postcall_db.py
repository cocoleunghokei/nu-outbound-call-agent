"""Post-call against the local Supabase stack: handlers, the jobs lane and what they write."""

import asyncio
import json
import uuid
from typing import Any

import psycopg
import pytest

from app.postcall import jobs as postcall_jobs
from app.postcall.jobs import run_due_jobs
from tests.calls_helpers import call_status, event_rows, make_app, outbox_rows, seed_call
from tests.conftest import FreshCall, SupabaseEnv
from tests.test_webhooks import fixture, post, rows, serve, tool_body, transcript_body

pytestmark = pytest.mark.db

BOOKED_ARGS = {
    "outcome": "booked",
    "business_confirmed": True,
    "details": {"date": "2026-10-24", "time": "19:30", "staff_name": "Marco"},
}


async def outcome_of(db: str, call: FreshCall) -> Any:
    found = await rows(db, "select outcome from public.calls where id = %s", (call.call_id,))
    return found[0][0]


async def shared_rows(db: str, call: FreshCall) -> list[tuple[Any, ...]]:
    return await rows(
        db,
        "select field_key, state from public.shared_fields where call_id = %s order by field_key",
        (call.call_id,),
    )


def ended_events(events: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [payload for kind, payload in events if kind == "call.ended"]


def test_end_of_call_report_ends_the_call_and_queues_each_job_once(
    supabase_env: SupabaseEnv,
) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await post(client, fixture("end-of-call-report", call))
            await post(client, fixture("end-of-call-report", call))  # Vapi redelivers
        assert await call_status(db, call) == "ended"
        jobs = [r for r in await outbox_rows(db, call) if r["lane"] == "jobs"]
        assert [(r["kind"], r["state"]) for r in jobs] == [
            ("outcome", "pending"),
            ("shared_fields", "pending"),
        ]
        assert jobs[0]["payload"] == {
            "ended_reason": "assistant-ended-call",
            "agent_lines": ["Hi, I'm calling for Alex Chen."],
            "business_lines": ["Sure."],
            "turns": [["agent", "Hi, I'm calling for Alex Chen."], ["business", "Sure."]],
        }

    asyncio.run(scenario())


def test_confirmed_booking_is_booked_and_call_ended_goes_out_once(
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
                    "Hi, I'm an AI assistant calling on behalf of Alex. A table for 2 Saturday?",
                    role="assistant",
                    timestamp=1794081601000,
                ),
            )
            await post(
                client,
                transcript_body(
                    call,
                    "Got it, party of 2 at 7:30 on Saturday, October 24th. I'm Marco.",
                    role="user",
                    timestamp=1794081609000,
                ),
            )
            tc = f"tc-{uuid.uuid4()}"
            reply = await post(client, tool_body(call, [(tc, "report_outcome", BOOKED_ARGS)]))
            assert reply.json()["results"][0]["toolCallId"] == tc
            await post(client, fixture("end-of-call-report", call))

            # The test database is shared, so a pass may also run other tests' jobs: check
            # this call's rows and events below, not how many jobs a pass finished.
            pool = app.state.pool
            await run_due_jobs(pool)
            await run_due_jobs(pool)  # a second pass must change nothing for this call

        assert await outcome_of(db, call) == "booked"
        events = await event_rows(db, call)
        assert ended_events(events) == [
            {"outcome": "booked", "ended_reason": "assistant-ended-call"}
        ]
        assert await shared_rows(db, call) == [("calling_name", "spoken")]
        spoken = [p for kind, p in events if kind == "field.shared" and p["state"] == "spoken"]
        assert spoken == [{"field_key": "calling_name", "state": "spoken"}]
        jobs = [r for r in await outbox_rows(db, call) if r["lane"] == "jobs"]
        assert {r["state"] for r in jobs} == {"sent"}

    asyncio.run(scenario())


def test_a_weekday_confirms_the_booking_from_the_call_times(supabase_env: SupabaseEnv) -> None:
    """The in-call eval's G-11: "Thursday at 2" on a call made Tue 17 Nov 2026 is 19 Nov."""
    db = supabase_env.db_url
    call = seed_call(db)
    thursday = {
        "outcome": "booked",
        "business_confirmed": True,
        "details": {"date": "2026-11-19", "time": "14:00"},
    }

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(
                client,
                transcript_body(
                    call, "You're all set for Thursday at 2.", role="user", timestamp=1794081609000
                ),
            )
            await post(
                client, tool_body(call, [(f"tc-{uuid.uuid4()}", "report_outcome", thursday)])
            )
            await post(client, fixture("end-of-call-report", call))
            # Pin the call to Tue 17 Nov 2026, 2pm in Boston, whatever today is.
            async with await psycopg.AsyncConnection.connect(db, autocommit=True) as conn:
                await conn.execute(
                    "update public.calls set created_at = %s, ended_at = %s where id = %s",
                    ("2026-11-17T19:00:00Z", "2026-11-17T19:05:00Z", call.call_id),
                )
            await run_due_jobs(app.state.pool)

        assert await outcome_of(db, call) == "booked"

    asyncio.run(scenario())


def test_booking_the_business_never_confirmed_needs_follow_up(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
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

        assert await outcome_of(db, call) == "needs_follow_up"
        assert ended_events(await event_rows(db, call))[0]["outcome"] == "needs_follow_up"

    asyncio.run(scenario())


def test_no_report_outcome_needs_follow_up(
    supabase_env: SupabaseEnv,
) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, fixture("end-of-call-report", call))
            await run_due_jobs(app.state.pool)

        assert await outcome_of(db, call) == "needs_follow_up"
        assert ended_events(await event_rows(db, call)) == [
            {"outcome": "needs_follow_up", "ended_reason": "assistant-ended-call"}
        ]
        # The report's own transcript has the agent saying "calling for Alex Chen".
        assert await shared_rows(db, call) == [("calling_name", "spoken")]

    asyncio.run(scenario())


def test_outcome_already_set_is_never_overwritten(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        await rows(
            db,
            "update public.calls set outcome = 'refused_ai' where id = %s returning id",
            (call.call_id,),
        )
        app = make_app(db)
        async with serve(app) as client:
            await post(client, fixture("end-of-call-report", call))
            await run_due_jobs(app.state.pool)

        assert await outcome_of(db, call) == "refused_ai"
        assert ended_events(await event_rows(db, call))[0]["outcome"] == "refused_ai"

    asyncio.run(scenario())


def report_with_lines(call: FreshCall, lines: list[tuple[str, str]]) -> dict[str, Any]:
    """The end-of-call-report fixture with its transcript replaced by ``(role, text)`` lines."""
    body = fixture("end-of-call-report", call)
    body["message"]["artifact"]["messages"] = [
        {"role": role, "message": text, "time": 1794081601000 + i, "secondsFromStart": float(i)}
        for i, (role, text) in enumerate(lines)
    ]
    return body


async def insert_pending_report(db: str, call: FreshCall, args: dict[str, Any]) -> None:
    """A report_outcome receipt still pending (its handler hasn't run yet)."""
    await rows(
        db,
        "insert into public.tool_calls (call_id, tool_call_id, name, arguments, state)"
        " values (%s, %s, 'report_outcome', %s::jsonb, 'pending') returning tool_call_id",
        (call.call_id, f"tc-{uuid.uuid4()}", json.dumps(args)),
    )


def test_business_refusal_in_the_report_transcript_is_not_booked(
    supabase_env: SupabaseEnv,
) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            tc = f"tc-{uuid.uuid4()}"
            await post(client, tool_body(call, [(tc, "report_outcome", BOOKED_ARGS)]))
            body = report_with_lines(call, [("user", "Sorry, Saturday at 7:30 is full.")])
            await post(client, body)
            await run_due_jobs(app.state.pool)

        assert await outcome_of(db, call) == "needs_follow_up"

    asyncio.run(scenario())


def test_pending_report_and_report_transcript_are_used_as_evidence(
    supabase_env: SupabaseEnv,
) -> None:
    # No transcript.final events arrived (late or lost webhooks), and the report_outcome
    # receipt is still pending: the report's own transcript and the pending args decide.
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        await insert_pending_report(db, call, BOOKED_ARGS)
        app = make_app(db)
        async with serve(app) as client:
            body = report_with_lines(
                call,
                [
                    ("bot", "Hi, I'm calling on behalf of Alex. A table for 2?"),
                    ("user", "Sure, 7:30 on October 24th works. You're booked."),
                ],
            )
            await post(client, body)
            await run_due_jobs(app.state.pool)

        assert await outcome_of(db, call) == "booked"
        assert await shared_rows(db, call) == [("calling_name", "spoken")]

    asyncio.run(scenario())


def test_outcome_job_that_keeps_failing_still_ends_the_call(
    supabase_env: SupabaseEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def broken(*_args: Any) -> None:
        raise RuntimeError("persistent failure")

    monkeypatch.setattr(postcall_jobs, "run_outcome_job", broken)

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app) as client:
            await post(client, fixture("end-of-call-report", call))
            for _ in range(postcall_jobs.MAX_ATTEMPTS):
                await rows(
                    db,
                    "update public.outbox set not_before = now()"
                    " where call_id = %s and lane = 'jobs' returning id",
                    (call.call_id,),
                )
                await run_due_jobs(app.state.pool)

        jobs = {r["kind"]: r for r in await outbox_rows(db, call) if r["lane"] == "jobs"}
        assert jobs["outcome"]["state"] == "failed"
        assert jobs["outcome"]["attempts"] == postcall_jobs.MAX_ATTEMPTS
        assert await outcome_of(db, call) == "needs_follow_up"
        assert ended_events(await event_rows(db, call)) == [
            {"outcome": "needs_follow_up", "ended_reason": postcall_jobs.GAVE_UP}
        ]

    asyncio.run(scenario())
