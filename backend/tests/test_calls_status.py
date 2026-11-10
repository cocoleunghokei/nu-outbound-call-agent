"""Vapi status-update -> call status (DC-14), and report_decision -> agent.decision (DC-4, DC-5)."""

import asyncio
import uuid
from typing import Any

import pytest

from tests.calls_helpers import call_status, event_rows, make_app, seed_call
from tests.conftest import FreshCall, SupabaseEnv
from tests.test_webhooks import post, rows, serve, tool_body

pytestmark = pytest.mark.db


def status_body(call: FreshCall, status: str) -> dict[str, Any]:
    return {
        "message": {
            "type": "status-update",
            "status": status,
            "timestamp": 1794081600000,
            "call": {"id": f"vapi-{call.call_id}", "metadata": {"call_id": str(call.call_id)}},
        }
    }


@pytest.mark.parametrize(
    ("start", "vapi_status", "expected"),
    [
        ("creating", "queued", "dialing"),
        ("creating", "ringing", "dialing"),
        ("dialing", "ringing", "dialing"),
        ("dialing", "in-progress", "talking"),
        ("creation_uncertain", "in-progress", "talking"),
        # Our own states are never overridden by Vapi.
        ("waiting_on_you", "in-progress", "waiting_on_you"),
        ("on_hold", "in-progress", "on_hold"),
        ("talking", "ringing", "talking"),
        # Unknown statuses are ignored.
        ("talking", "forwarding", "talking"),
    ],
)
def test_status_update_moves_only_forward(
    supabase_env: SupabaseEnv, start: str, vapi_status: str, expected: str
) -> None:
    db = supabase_env.db_url
    call = seed_call(db, status=start)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await post(client, status_body(call, vapi_status))
            await post(client, status_body(call, vapi_status))  # redelivery
        assert await call_status(db, call) == expected
        changes = [p for t, p in await event_rows(db, call) if t == "status.changed"]
        assert changes == ([{"status": expected}] if expected != start else [])

    asyncio.run(scenario())


def test_ended_status_ends_the_call_once_and_for_good(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        async with serve(make_app(db)) as client:
            await post(client, status_body(call, "ended"))
            await post(client, status_body(call, "ended"))
            await post(client, status_body(call, "in-progress"))  # late: can't revive
        assert await call_status(db, call) == "ended"
        changes = [p for t, p in await event_rows(db, call) if t == "status.changed"]
        assert changes == [{"status": "ended"}]
        secrets = await rows(
            db, "select 1 from public.call_secrets where call_id = %s", (call.call_id,)
        )
        assert secrets == []

    asyncio.run(scenario())


def _decision_args(**extra: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "situation": "They offered 7pm on the patio",
        "action": "accept",
        "reason": "Meets your time and party size.",
        "confidence": "medium",
        "dimensions": {
            "needs_ask_first_field": False,
            "involves_money": False,
            "meets_constraints": True,
            "matches_fallback": False,
            "answerable_from_brief": True,
            "understood": True,
        },
    }
    args.update(extra)
    return args


def test_report_decision_logs_the_reason_never_the_score(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        tc_id = f"tc-{uuid.uuid4()}"
        body = tool_body(
            call,
            [(tc_id, "report_decision", _decision_args(missed_preference="No window seat"))],
        )
        async with serve(make_app(db)) as client:
            first = await post(client, body)
            await post(client, body)  # redelivery logs nothing new
        assert first.json()["results"] == [{"toolCallId": tc_id, "result": "Noted."}]
        decisions = [p for t, p in await event_rows(db, call) if t == "agent.decision"]
        assert decisions == [
            {
                "situation": "They offered 7pm on the patio",
                "action": "accept",
                "reason": "Meets your time and party size.",
                "missed_preference": "No window seat",
            }
        ]
        scores = await rows(
            db, "select confidence from public.agent_scores where tool_call_id = %s", (tc_id,)
        )
        assert scores == [("medium",)]  # kept backend-only

    asyncio.run(scenario())


def test_report_decision_keeps_field_key_for_shared_fields(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = seed_call(db)

    async def scenario() -> None:
        body = tool_body(
            call,
            [
                (
                    f"tc-{uuid.uuid4()}",
                    "report_decision",
                    _decision_args(action="share_field", field_key="email"),
                )
            ],
        )
        async with serve(make_app(db)) as client:
            await post(client, body)
        [decision] = [p for t, p in await event_rows(db, call) if t == "agent.decision"]
        assert decision["action"] == "share_field" and decision["field_key"] == "email"

    asyncio.run(scenario())
