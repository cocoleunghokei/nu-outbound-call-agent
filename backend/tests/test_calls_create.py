"""POST /calls against the real local Supabase stack, with the fake voice platform.

Covers the call-creation rule in docs/architecture.md section 4.1: one Vapi call per Start,
the profile frozen into the call's snapshot, and explicit ``failed`` and
``creation_uncertain`` states instead of a retry. All data is fake.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.calls.create import get_call_voice
from app.config import Settings
from app.main import create_app
from app.voice.fake import FakeVoicePlatform
from app.voice.port import CreatedCall, VoiceNotSent, VoiceRejected, VoiceUncertain
from app.voice.prompt_safety import Snapshot
from tests.conftest import SignedUpUser, SupabaseEnv
from tests.test_briefs_db import ALEX, auth, create, set_profile, sign_up
from tests.test_webhooks import registries

pytestmark = pytest.mark.db


def settings_for(env: SupabaseEnv, **overrides: Any) -> Settings:
    return Settings(
        database_url=env.db_url,
        supabase_jwt_secret=env.jwt_secret,  # type: ignore[arg-type]
        supabase_jwt_issuer=env.jwt_issuer,
        supabase_jwks_url=f"{env.auth_url}/.well-known/jwks.json",
        **overrides,
    )


class UniqueFakeVoice(FakeVoicePlatform):
    """The fake platform with globally unique call ids: every test shares one database, and
    ``calls.vapi_call_id`` is unique."""

    async def create_call(
        self,
        assistant: dict[str, Any],
        customer_e164: str,
        metadata: dict[str, Any],
        snapshot: Snapshot,
    ) -> CreatedCall:
        created = await super().create_call(assistant, customer_e164, metadata, snapshot)
        created.vapi_call_id = f"fake-{uuid.uuid4()}"
        return created


@pytest.fixture
def voice() -> FakeVoicePlatform:
    return UniqueFakeVoice()


@pytest.fixture
def client(supabase_env: SupabaseEnv, voice: FakeVoicePlatform) -> Iterator[TestClient]:
    app = create_app(settings_for(supabase_env))
    registries(app).workers.clear()  # no outbox or sweeper: the tests read the rows as left
    app.dependency_overrides[get_call_voice] = lambda: voice
    with TestClient(app) as test_client:
        yield test_client


def start(
    client: TestClient, user: SignedUpUser, brief_id: str, key: uuid.UUID | None = None
) -> Any:
    return client.post(
        "/calls", json={"brief_id": brief_id}, headers=auth(user, key or uuid.uuid4())
    )


def ready_brief(client: TestClient, env: SupabaseEnv, user: SignedUpUser) -> str:
    set_profile(env, user.user_id, ALEX)
    created = create(client, user)
    assert created["missing_fields"] == []
    return str(created["brief_id"])


def call_row(env: SupabaseEnv, call_id: str) -> dict[str, Any]:
    with psycopg.connect(env.db_url) as conn:
        row = conn.execute(
            "select user_id, brief_id, status, outcome, vapi_call_id, create_attempted_at,"
            " ended_at from public.calls where id = %s",
            (call_id,),
        ).fetchone()
    assert row is not None
    keys = "user_id brief_id status outcome vapi_call_id create_attempted_at ended_at".split()
    return dict(zip(keys, row, strict=True))


def statuses(env: SupabaseEnv, call_id: str) -> list[str]:
    with psycopg.connect(env.db_url) as conn:
        found = conn.execute(
            "select payload->>'status' from public.call_events"
            " where call_id = %s and type = 'status.changed' order by seq",
            (call_id,),
        ).fetchall()
    return [r[0] for r in found]


def calls_for_brief(env: SupabaseEnv, brief_id: str) -> int:
    with psycopg.connect(env.db_url) as conn:
        row = conn.execute(
            "select count(*) from public.calls where brief_id = %s", (brief_id,)
        ).fetchone()
    assert row is not None
    return int(row[0])


def test_start_places_one_call_and_returns_its_id(
    client: TestClient,
    supabase_env: SupabaseEnv,
    signed_up_user: SignedUpUser,
    voice: FakeVoicePlatform,
) -> None:
    brief_id = ready_brief(client, supabase_env, signed_up_user)

    response = start(client, signed_up_user, brief_id)

    assert response.status_code == 201, response.text
    body = response.json()
    call_id = body["call_id"]
    assert body == {"call_id": call_id, "status": "dialing"}

    row = call_row(supabase_env, call_id)
    assert row["user_id"] == signed_up_user.user_id
    assert str(row["brief_id"]) == brief_id
    assert row["status"] == "dialing"
    assert row["vapi_call_id"] == voice.calls[0]["created"].vapi_call_id
    assert row["create_attempted_at"] is not None
    assert statuses(supabase_env, call_id) == ["creating", "dialing"]

    # Exactly one request, to the brief's number, tagged with our call id.
    assert len(voice.calls) == 1
    sent = voice.calls[0]
    assert sent["customer"] == "+14155550123"
    assert sent["metadata"] == {"call_id": call_id}
    assistant = sent["assistant"]
    assert assistant["firstMessage"].startswith(
        "Hi, I'm an AI assistant calling on behalf of Alex."
    )
    assert assistant["server"]["url"].endswith("/webhooks/vapi")
    # The Ask-me-first date of birth never reaches the model.
    assert ALEX["date_of_birth"][0] not in str(assistant)

    with psycopg.connect(supabase_env.db_url) as conn:
        snapshot = conn.execute(
            "select key, value, sharing from public.call_profile_snapshots"
            " where call_id = %s order by key",
            (call_id,),
        ).fetchall()
        secrets = conn.execute(
            "select control_url from public.call_secrets where call_id = %s", (call_id,)
        ).fetchone()
    assert snapshot == sorted((k, v, s) for k, (v, s) in ALEX.items())
    assert secrets == ("https://fake.invalid/control/1",)


def test_replaying_the_same_key_returns_the_same_call(
    client: TestClient,
    supabase_env: SupabaseEnv,
    signed_up_user: SignedUpUser,
    voice: FakeVoicePlatform,
) -> None:
    brief_id = ready_brief(client, supabase_env, signed_up_user)
    key = uuid.uuid4()

    first = start(client, signed_up_user, brief_id, key)
    again = start(client, signed_up_user, brief_id, key)

    assert first.status_code == again.status_code == 201
    assert again.json() == first.json()
    assert len(voice.calls) == 1
    assert calls_for_brief(supabase_env, brief_id) == 1


def test_a_second_start_while_the_call_runs_is_refused(
    client: TestClient,
    supabase_env: SupabaseEnv,
    signed_up_user: SignedUpUser,
    voice: FakeVoicePlatform,
) -> None:
    brief_id = ready_brief(client, supabase_env, signed_up_user)
    assert start(client, signed_up_user, brief_id).status_code == 201

    second = start(client, signed_up_user, brief_id)  # new key: a second tap, not a retry

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "call_in_progress"
    assert len(voice.calls) == 1


@pytest.mark.parametrize("exc", [VoiceRejected(400, "bad_number"), VoiceNotSent("offline")])
def test_a_call_that_was_not_placed_is_failed_and_can_be_started_again(
    client: TestClient,
    supabase_env: SupabaseEnv,
    signed_up_user: SignedUpUser,
    voice: FakeVoicePlatform,
    exc: Exception,
) -> None:
    brief_id = ready_brief(client, supabase_env, signed_up_user)
    voice.fail_next(exc)

    response = start(client, signed_up_user, brief_id)

    assert response.status_code == 201, response.text
    call_id = response.json()["call_id"]
    assert response.json()["status"] == "failed"
    row = call_row(supabase_env, call_id)
    assert (row["status"], row["outcome"], row["vapi_call_id"]) == ("failed", "failed", None)
    assert row["ended_at"] is not None
    assert statuses(supabase_env, call_id) == ["creating", "failed"]

    # The failed call is over, so the same brief can be started again.
    retry = start(client, signed_up_user, brief_id)
    assert retry.status_code == 201
    assert retry.json()["status"] == "dialing"
    assert retry.json()["call_id"] != call_id


def test_an_unknown_outcome_is_marked_uncertain_and_never_resent(
    client: TestClient,
    supabase_env: SupabaseEnv,
    signed_up_user: SignedUpUser,
    voice: FakeVoicePlatform,
) -> None:
    brief_id = ready_brief(client, supabase_env, signed_up_user)
    voice.fail_next(VoiceUncertain("timeout"))
    key = uuid.uuid4()

    response = start(client, signed_up_user, brief_id, key)

    assert response.status_code == 201
    call_id = response.json()["call_id"]
    assert response.json()["status"] == "creation_uncertain"
    row = call_row(supabase_env, call_id)
    assert (row["status"], row["ended_at"]) == ("creation_uncertain", None)
    assert statuses(supabase_env, call_id) == ["creating", "creation_uncertain"]

    # Neither a replay nor a new tap sends a second request while it may be ringing.
    assert start(client, signed_up_user, brief_id, key).json() == response.json()
    assert start(client, signed_up_user, brief_id).status_code == 409
    assert voice.calls == []


def test_another_users_brief_is_not_found(
    client: TestClient,
    supabase_env: SupabaseEnv,
    signed_up_user: SignedUpUser,
    voice: FakeVoicePlatform,
) -> None:
    brief_id = ready_brief(client, supabase_env, signed_up_user)
    bob = sign_up(supabase_env)

    response = start(client, bob, brief_id)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "brief_not_found"
    assert calls_for_brief(supabase_env, brief_id) == 0
    assert voice.calls == []


def test_a_brief_that_is_no_longer_ready_is_refused(
    client: TestClient,
    supabase_env: SupabaseEnv,
    signed_up_user: SignedUpUser,
    voice: FakeVoicePlatform,
) -> None:
    brief_id = ready_brief(client, supabase_env, signed_up_user)
    # The profile changed after the brief was saved: no calling name any more.
    with psycopg.connect(supabase_env.db_url, autocommit=True) as conn:
        conn.execute(
            "delete from public.profile_fields where user_id = %s and key = 'calling_name'",
            (signed_up_user.user_id,),
        )

    response = start(client, signed_up_user, brief_id)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "brief_not_ready"
    assert "calling_name" in response.json()["error"]["message"]
    assert calls_for_brief(supabase_env, brief_id) == 0
    assert voice.calls == []


def test_the_number_is_checked_again_on_the_server(
    client: TestClient,
    supabase_env: SupabaseEnv,
    signed_up_user: SignedUpUser,
    voice: FakeVoicePlatform,
) -> None:
    brief_id = ready_brief(client, supabase_env, signed_up_user)
    # Written behind the API's back: a premium-rate number the brief API would refuse.
    with psycopg.connect(supabase_env.db_url, autocommit=True) as conn:
        conn.execute(
            "update public.briefs set brief = jsonb_set(brief, '{phone_e164}', '\"+19005550199\"')"
            " where id = %s",
            (brief_id,),
        )

    response = start(client, signed_up_user, brief_id)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_phone"
    assert calls_for_brief(supabase_env, brief_id) == 0
    assert voice.calls == []


def test_real_calls_need_the_public_webhook_url(
    supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    app = create_app(
        settings_for(
            supabase_env,
            voice_platform="vapi",
            vapi_webhook_secret="test-webhook-secret",
            vapi_phone_number_id="pn_test",
        )
    )
    registries(app).workers.clear()
    voice = UniqueFakeVoice()
    app.dependency_overrides[get_call_voice] = lambda: voice
    with TestClient(app) as client:
        brief_id = ready_brief(client, supabase_env, signed_up_user)
        response = start(client, signed_up_user, brief_id)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "calls_not_configured"
    assert calls_for_brief(supabase_env, brief_id) == 0
    assert voice.calls == []
