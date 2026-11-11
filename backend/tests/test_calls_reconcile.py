"""The reconciler (app/calls/reconcile.py) against the real local Supabase stack.

A call whose creation got no clear answer must never block its brief forever, and must never
be dialed twice: it is adopted if the voice platform did place it, and failed once it can't
still be ringing. All data is fake.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from app.calls.create import record_not_placed
from app.calls.reconcile import (
    CHECKING_S,
    GIVE_UP_AFTER_S,
    STALE_CREATING_S,
    mark_stale_creating,
    resolve_uncertain,
)
from app.voice.fake import FakeVoicePlatform
from app.voice.port import CreatedCall, VoiceUncertain
from tests.calls_helpers import outbox_rows, seed_call
from tests.conftest import FreshCall, SupabaseEnv

pytestmark = pytest.mark.db

MAX_DURATION_S = 900  # the calls.max_duration_s default


def stuck_call(db_url: str, status: str, attempted_s_ago: float) -> FreshCall:
    """A call in ``status`` with no Vapi id, whose create request went out ``attempted_s_ago``."""
    call = seed_call(db_url)
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(
            "update public.calls set status = %s, vapi_call_id = null,"
            " create_attempted_at = now() - make_interval(secs => %s) where id = %s",
            (status, attempted_s_ago, call.call_id),
        )
        conn.execute("delete from public.call_secrets where call_id = %s", (call.call_id,))
    return call


def placed_at_vapi(voice: FakeVoicePlatform, call: FreshCall) -> CreatedCall:
    """Make the fake platform hold a call tagged with our call_id, as if Vapi had placed it."""
    created = CreatedCall(
        f"vapi-found-{uuid.uuid4()}", f"https://fake.invalid/control/{call.call_id}", None
    )
    voice.calls.append({"metadata": {"call_id": str(call.call_id)}, "created": created})
    return created


def call_state(db_url: str, call: FreshCall) -> dict[str, Any]:
    with psycopg.connect(db_url) as conn:
        row = conn.execute(
            "select c.status, c.outcome, c.vapi_call_id, c.ended_at, s.control_url"
            " from public.calls c left join public.call_secrets s on s.call_id = c.id"
            " where c.id = %s",
            (call.call_id,),
        ).fetchone()
        events = conn.execute(
            "select payload->>'status' from public.call_events"
            " where call_id = %s and type = 'status.changed' order by seq",
            (call.call_id,),
        ).fetchall()
    assert row is not None
    keys = "status outcome vapi_call_id ended_at control_url".split()
    return {**dict(zip(keys, row, strict=True)), "events": [e[0] for e in events]}


def run(db_url: str, body: Callable[[AsyncConnectionPool[Any]], Awaitable[Any]]) -> Any:
    async def scenario() -> Any:
        async with AsyncConnectionPool(db_url, min_size=1, max_size=4, open=False) as pool:
            return await body(pool)

    return asyncio.run(scenario())


@pytest.fixture(autouse=True)
def quiet_other_calls(supabase_env: SupabaseEnv) -> None:
    """Fail stuck calls other tests left behind, so each pass only sees this test's call."""
    with psycopg.connect(supabase_env.db_url, autocommit=True) as conn:
        conn.execute(
            "update public.calls set status = 'failed', ended_at = now()"
            " where status in ('creating', 'creation_uncertain')"
        )


def test_a_call_stuck_in_creating_becomes_uncertain(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    stuck = stuck_call(db, "creating", STALE_CREATING_S + 5)
    in_flight = stuck_call(db, "creating", 2)  # its request may still be on the wire

    assert run(db, mark_stale_creating) == 1

    assert call_state(db, stuck)["status"] == "creation_uncertain"
    assert call_state(db, stuck)["events"] == ["creation_uncertain"]
    assert call_state(db, in_flight)["status"] == "creating"


def test_an_uncertain_call_found_at_vapi_is_adopted(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = stuck_call(db, "creation_uncertain", 30)
    voice = FakeVoicePlatform()
    created = placed_at_vapi(voice, call)

    assert run(db, lambda pool: resolve_uncertain(pool, voice)) == 1

    state = call_state(db, call)
    assert state["status"] == "dialing"
    assert state["vapi_call_id"] == created.vapi_call_id
    assert state["control_url"] == created.control_url
    assert state["ended_at"] is None
    assert [o for o in run(db, lambda _: outbox_rows(db, call)) if o["lane"] == "control"] == []


def test_a_call_found_after_the_screen_said_failed_is_hung_up(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    call = stuck_call(db, "creation_uncertain", CHECKING_S + 10)
    voice = FakeVoicePlatform()
    placed_at_vapi(voice, call)

    assert run(db, lambda pool: resolve_uncertain(pool, voice)) == 1

    assert call_state(db, call)["status"] == "dialing"  # ended when Vapi reports the hang-up
    kinds = [o["kind"] for o in run(db, lambda _: outbox_rows(db, call)) if o["lane"] == "control"]
    assert len(kinds) == 2  # the apology with endCallAfterSpoken, then end-call as a fallback


def test_an_uncertain_call_is_kept_while_it_could_still_be_ringing(
    supabase_env: SupabaseEnv,
) -> None:
    db = supabase_env.db_url
    call = stuck_call(db, "creation_uncertain", 60)
    voice = FakeVoicePlatform()

    assert run(db, lambda pool: resolve_uncertain(pool, voice)) == 0
    assert call_state(db, call)["status"] == "creation_uncertain"


def test_an_uncertain_call_never_found_is_failed_and_frees_the_brief(
    supabase_env: SupabaseEnv,
) -> None:
    db = supabase_env.db_url
    call = stuck_call(db, "creation_uncertain", MAX_DURATION_S + GIVE_UP_AFTER_S + 5)
    voice = FakeVoicePlatform()

    assert run(db, lambda pool: resolve_uncertain(pool, voice)) == 1

    state = call_state(db, call)
    assert (state["status"], state["outcome"]) == ("failed", "failed")
    assert state["ended_at"] is not None
    assert state["events"] == ["failed"]
    assert voice.calls == []  # looked up only: nothing was ever dialed again


def test_a_failing_lookup_waits_until_the_limit_then_fails(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    young = stuck_call(db, "creation_uncertain", 60)
    old = stuck_call(db, "creation_uncertain", MAX_DURATION_S + GIVE_UP_AFTER_S + 5)
    voice = FakeVoicePlatform()
    voice.fail_next(VoiceUncertain("timeout"))
    voice.fail_next(VoiceUncertain("timeout"))

    assert run(db, lambda pool: resolve_uncertain(pool, voice)) == 1

    assert call_state(db, young)["status"] == "creation_uncertain"
    assert call_state(db, old)["status"] == "failed"


def test_a_provable_failure_settles_a_call_the_reconciler_marked_uncertain(
    supabase_env: SupabaseEnv,
) -> None:
    """POST /calls learns "not placed" after the reconciler already moved the call on."""
    db = supabase_env.db_url
    call = stuck_call(db, "creation_uncertain", STALE_CREATING_S + 1)

    async def body(pool: AsyncConnectionPool[Any]) -> str:
        async with pool.connection() as conn, conn.transaction():
            return await record_not_placed(conn, call.call_id, uncertain=False)

    assert run(db, body) == "failed"
    assert call_state(db, call)["status"] == "failed"
