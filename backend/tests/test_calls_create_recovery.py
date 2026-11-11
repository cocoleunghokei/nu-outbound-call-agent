"""The sweeper's recovery of call creations whose outcome is unknown (docs/architecture.md
section 4.1, "Call creation (E6)"), against the real local Supabase stack with the fake voice
platform.

A ``creating`` call with no recorded answer after 15 s becomes ``creation_uncertain``. Each
uncertain call is looked up by our ``call_id`` (never resent) until it is found (``dialing``,
or adopted and ended with an apology after 2 min) or the max call length plus 5 min has passed
(``failed``). All data is fake.
"""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.calls.create import get_call_voice
from app.calls.sweeper import (
    CREATE_STALL_S,
    GIVE_UP_GRACE_S,
    mark_stalled_creations,
    resolve_uncertain_creations,
)
from app.main import create_app
from app.voice.fake import FakeVoicePlatform
from app.voice.port import CreatedCall, VoiceNotSent, VoiceUncertain
from app.voice.prompt_safety import Snapshot
from tests.calls_helpers import event_rows, make_app, outbox_rows
from tests.conftest import FreshCall, SignedUpUser, SupabaseEnv, create_fresh_call
from tests.test_calls_create import UniqueFakeVoice, ready_brief, settings_for, start
from tests.test_webhooks import registries, rows, serve

pytestmark = pytest.mark.db

MAX_DURATION_S = 900
# A minimal assistant config the fake platform accepts (recording off, transcripts on).
SAFE_ASSISTANT: dict[str, Any] = {
    "artifactPlan": {"recordingEnabled": False, "transcriptPlan": {"enabled": True}},
}


def seed_creation(db_url: str, *, status: str, attempted_s_ago: float) -> FreshCall:
    """A call whose create request went out ``attempted_s_ago`` seconds ago and whose answer
    was never recorded: no ``vapi_call_id``, no control URL."""
    call = create_fresh_call(db_url)
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(
            "update public.calls set status = %s, vapi_call_id = null, max_duration_s = %s,"
            " create_attempted_at = now() - make_interval(secs => %s) where id = %s",
            (status, MAX_DURATION_S, attempted_s_ago, call.call_id),
        )
        conn.execute(
            "insert into public.call_profile_snapshots (call_id, key, value, sharing)"
            " values (%s, 'calling_name', 'Alex', 'share_freely')",
            (call.call_id,),
        )
    return call


async def place_on_platform(voice: FakeVoicePlatform, call: FreshCall) -> CreatedCall:
    """Make the fake platform hold a call for ``call``, as if Vapi had placed it."""
    return await voice.create_call(
        SAFE_ASSISTANT, "+15555550123", {"call_id": str(call.call_id)}, Snapshot({})
    )


async def call_columns(db_url: str, call: FreshCall) -> dict[str, Any]:
    found = await rows(
        db_url,
        "select status, outcome, vapi_call_id, ended_at from public.calls where id = %s",
        (call.call_id,),
    )
    return dict(zip(("status", "outcome", "vapi_call_id", "ended_at"), found[0], strict=True))


async def control_url(db_url: str, call: FreshCall) -> str | None:
    found = await rows(
        db_url, "select control_url from public.call_secrets where call_id = %s", (call.call_id,)
    )
    return str(found[0][0]) if found else None


async def statuses(db_url: str, call: FreshCall) -> list[str]:
    return [p["status"] for t, p in await event_rows(db_url, call) if t == "status.changed"]


async def set_attempted_s_ago(db_url: str, call: FreshCall, seconds: float) -> None:
    async with await psycopg.AsyncConnection.connect(db_url, autocommit=True) as conn:
        await conn.execute(
            "update public.calls set create_attempted_at = now() - make_interval(secs => %s)"
            " where id = %s",
            (seconds, call.call_id),
        )


def test_a_stalled_creation_becomes_uncertain_after_15_s(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    fresh = seed_creation(db, status="creating", attempted_s_ago=CREATE_STALL_S - 5)
    stalled = seed_creation(db, status="creating", attempted_s_ago=CREATE_STALL_S + 1)

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app):
            await mark_stalled_creations(app.state.pool)
            await mark_stalled_creations(app.state.pool)  # a second pass changes nothing
        assert (await call_columns(db, fresh))["status"] == "creating"
        assert (await call_columns(db, stalled))["status"] == "creation_uncertain"
        assert await statuses(db, stalled) == ["creation_uncertain"]

    asyncio.run(scenario())


def test_a_creating_call_with_its_answer_recorded_is_left_alone(
    supabase_env: SupabaseEnv,
) -> None:
    db = supabase_env.db_url
    call = seed_creation(db, status="creating", attempted_s_ago=60)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "update public.calls set vapi_call_id = %s where id = %s",
            (f"vapi-{call.call_id}", call.call_id),
        )

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app):
            assert await mark_stalled_creations(app.state.pool) >= 0
        assert (await call_columns(db, call))["status"] == "creating"

    asyncio.run(scenario())


def test_a_found_call_is_adopted_and_dialing_without_a_second_create(
    supabase_env: SupabaseEnv,
) -> None:
    db = supabase_env.db_url
    voice = UniqueFakeVoice()
    call = seed_creation(db, status="creation_uncertain", attempted_s_ago=30)

    async def scenario() -> None:
        created = await place_on_platform(voice, call)
        app = make_app(db)
        async with serve(app):
            await resolve_uncertain_creations(app.state.pool, voice)
            await resolve_uncertain_creations(app.state.pool, voice)  # only once
        columns = await call_columns(db, call)
        assert (columns["status"], columns["vapi_call_id"]) == ("dialing", created.vapi_call_id)
        assert columns["ended_at"] is None
        assert await control_url(db, call) == created.control_url
        assert await statuses(db, call) == ["dialing"]
        assert await outbox_rows(db, call) == []  # in time: no apology
        assert len(voice.calls) == 1  # never resent

    asyncio.run(scenario())


def test_a_call_found_after_2_min_is_ended_at_once_with_an_apology(
    supabase_env: SupabaseEnv,
) -> None:
    db = supabase_env.db_url
    voice = UniqueFakeVoice()
    call = seed_creation(db, status="creation_uncertain", attempted_s_ago=121)

    async def scenario() -> None:
        created = await place_on_platform(voice, call)
        app = make_app(db)
        async with serve(app):
            await resolve_uncertain_creations(app.state.pool, voice)
            await resolve_uncertain_creations(app.state.pool, voice)  # only once
        columns = await call_columns(db, call)
        assert (columns["status"], columns["vapi_call_id"]) == ("dialing", created.vapi_call_id)
        assert await control_url(db, call) == created.control_url
        control = [r for r in await outbox_rows(db, call) if r["lane"] == "control"]
        assert [r["kind"] for r in control] == ["goodbye", "hangup"]
        [say] = control[0]["payload"]["messages"]
        assert "by mistake" in say["content"] and say["endCallAfterSpoken"] is True
        decisions = [p for t, p in await event_rows(db, call) if t == "agent.decision"]
        assert decisions == [
            {
                "situation": "The call was confirmed more than 2 minutes after it was started",
                "action": "end_call",
                "reason": "It had already been shown as failed, so it was ended with an apology.",
            }
        ]
        assert len(voice.calls) == 1

    asyncio.run(scenario())


def test_a_call_never_found_fails_after_the_max_length_plus_5_min(
    supabase_env: SupabaseEnv,
) -> None:
    db = supabase_env.db_url
    voice = UniqueFakeVoice()
    deadline_s = MAX_DURATION_S + GIVE_UP_GRACE_S
    call = seed_creation(db, status="creation_uncertain", attempted_s_ago=deadline_s - 10)

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app):
            await resolve_uncertain_creations(app.state.pool, voice)
            assert (await call_columns(db, call))["status"] == "creation_uncertain"
            await set_attempted_s_ago(db, call, deadline_s + 1)
            await resolve_uncertain_creations(app.state.pool, voice)
            await resolve_uncertain_creations(app.state.pool, voice)  # only once
        columns = await call_columns(db, call)
        assert (columns["status"], columns["outcome"]) == ("failed", "failed")
        assert columns["ended_at"] is not None and columns["vapi_call_id"] is None
        assert await statuses(db, call) == ["failed"]
        assert voice.calls == []  # nothing was ever (re)sent

    asyncio.run(scenario())


class BrokenLookup(UniqueFakeVoice):
    """The fake platform whose lookups raise ``lookup_error`` while it is set."""

    def __init__(self) -> None:
        super().__init__()
        self.lookup_error: Exception | None = None
        self.lookups = 0

    async def find_call_by_app_id(
        self, call_id: uuid.UUID, created_after: datetime
    ) -> CreatedCall | None:
        self.lookups += 1
        if self.lookup_error is not None:
            raise self.lookup_error
        return await super().find_call_by_app_id(call_id, created_after)


@pytest.mark.parametrize("exc", [VoiceUncertain("timeout"), VoiceNotSent("offline")])
def test_a_failed_lookup_is_retried_on_the_next_pass(
    supabase_env: SupabaseEnv, exc: Exception
) -> None:
    db = supabase_env.db_url
    voice = BrokenLookup()
    # Past the deadline: only a lookup that answers "not found" may fail the call.
    call = seed_creation(
        db, status="creation_uncertain", attempted_s_ago=MAX_DURATION_S + GIVE_UP_GRACE_S + 60
    )

    async def scenario() -> None:
        created = await place_on_platform(voice, call)
        app = make_app(db)
        async with serve(app):
            voice.lookup_error = exc
            await resolve_uncertain_creations(app.state.pool, voice)
            assert voice.lookups >= 1
            assert (await call_columns(db, call))["status"] == "creation_uncertain"
            voice.lookup_error = None
            await resolve_uncertain_creations(app.state.pool, voice)
        columns = await call_columns(db, call)
        assert (columns["status"], columns["vapi_call_id"]) == ("dialing", created.vapi_call_id)
        assert len(voice.calls) == 1  # never resent

    asyncio.run(scenario())


def test_an_ended_call_is_not_adopted(supabase_env: SupabaseEnv) -> None:
    db = supabase_env.db_url
    voice = UniqueFakeVoice()
    call = seed_creation(db, status="creation_uncertain", attempted_s_ago=30)

    async def scenario() -> None:
        await place_on_platform(voice, call)
        async with await psycopg.AsyncConnection.connect(db, autocommit=True) as conn:
            # A webhook ended it between the read and the lookup.
            await conn.execute(
                "update public.calls set status = 'ended', ended_at = now() where id = %s",
                (call.call_id,),
            )
        app = make_app(db)
        async with serve(app):
            await resolve_uncertain_creations(app.state.pool, voice)
        columns = await call_columns(db, call)
        assert (columns["status"], columns["vapi_call_id"]) == ("ended", None)
        assert await control_url(db, call) is None

    asyncio.run(scenario())


class PlacesThenTimesOut(UniqueFakeVoice):
    """Vapi placed the call, but its answer never reached us."""

    async def create_call(
        self,
        assistant: dict[str, Any],
        customer_e164: str,
        metadata: dict[str, Any],
        snapshot: Snapshot,
    ) -> CreatedCall:
        await super().create_call(assistant, customer_e164, metadata, snapshot)
        raise VoiceUncertain("timeout")


@pytest.fixture
def timing_out_voice() -> FakeVoicePlatform:
    return PlacesThenTimesOut()


@pytest.fixture
def timing_out_client(
    supabase_env: SupabaseEnv, timing_out_voice: FakeVoicePlatform
) -> Iterator[TestClient]:
    app = create_app(settings_for(supabase_env))
    registries(app).workers.clear()
    app.dependency_overrides[get_call_voice] = lambda: timing_out_voice
    with TestClient(app) as test_client:
        yield test_client


def test_post_calls_timeout_is_resolved_by_the_sweeper(
    timing_out_client: TestClient,
    supabase_env: SupabaseEnv,
    signed_up_user: SignedUpUser,
    timing_out_voice: FakeVoicePlatform,
) -> None:
    db = supabase_env.db_url
    brief_id = ready_brief(timing_out_client, supabase_env, signed_up_user)
    response = start(timing_out_client, signed_up_user, brief_id)
    assert response.json()["status"] == "creation_uncertain"
    call = FreshCall(
        call_id=uuid.UUID(response.json()["call_id"]),
        user_id=signed_up_user.user_id,
        brief_id=uuid.UUID(brief_id),
    )

    async def scenario() -> None:
        app = make_app(db)
        async with serve(app):
            await resolve_uncertain_creations(app.state.pool, timing_out_voice)
        columns = await call_columns(db, call)
        assert columns["status"] == "dialing"
        assert columns["vapi_call_id"] == timing_out_voice.calls[0]["created"].vapi_call_id
        assert await statuses(db, call) == ["creating", "creation_uncertain", "dialing"]
        assert len(timing_out_voice.calls) == 1

    asyncio.run(scenario())
