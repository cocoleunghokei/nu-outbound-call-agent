"""Brief API against the real local Supabase stack (make test-db; CI runs it on every PR).

The backend connects as the database owner, so RLS can't protect briefs here: these tests
prove the API itself never lets user B read or change user A's brief.
"""

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.briefs.store import brief_readiness
from app.config import Settings
from app.main import create_app
from tests.conftest import SignedUpUser, SupabaseEnv
from tests.test_briefs import example_brief

pytestmark = pytest.mark.db


def sign_up(env: SupabaseEnv) -> SignedUpUser:
    """A second fresh fake user (the conftest fixture gives one per test)."""
    response = httpx.post(
        f"{env.auth_url}/signup",
        json={"email": f"bob+{uuid.uuid4()}@example.test", "password": "correct-horse-battery"},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return SignedUpUser(user_id=uuid.UUID(body["user"]["id"]), access_token=body["access_token"])


def set_profile(env: SupabaseEnv, user_id: uuid.UUID, fields: dict[str, tuple[str, str]]) -> None:
    """Write fake profile fields via the owner connection (no profile API yet)."""
    with psycopg.connect(env.db_url, autocommit=True) as conn:
        conn.execute(
            "insert into public.profiles (id) values (%s) on conflict do nothing", (user_id,)
        )
        for key, (value, sharing) in fields.items():
            conn.execute(
                "insert into public.profile_fields (user_id, key, value, sharing)"
                " values (%s, %s, %s, %s) on conflict (user_id, key)"
                " do update set value = excluded.value, sharing = excluded.sharing",
                (user_id, key, value, sharing),
            )


ALEX = {"calling_name": ("Alex", "share_freely"), "date_of_birth": ("2004-03-15", "ask_first")}


@pytest.fixture
def client(supabase_env: SupabaseEnv) -> Iterator[TestClient]:
    settings = Settings(
        database_url=supabase_env.db_url,
        supabase_jwt_secret=supabase_env.jwt_secret,  # type: ignore[arg-type]
        supabase_jwt_issuer=supabase_env.jwt_issuer,
        supabase_jwks_url=f"{supabase_env.auth_url}/.well-known/jwks.json",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def auth(user: SignedUpUser, key: uuid.UUID | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {user.access_token}"}
    if key is not None:
        headers["Idempotency-Key"] = str(key)
    return headers


def create(client: TestClient, user: SignedUpUser, brief: dict[str, Any] | None = None) -> Any:
    response = client.post(
        "/briefs", json=brief or example_brief(), headers=auth(user, uuid.uuid4())
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_then_read(
    client: TestClient, supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    set_profile(supabase_env, signed_up_user.user_id, ALEX)
    created = create(client, signed_up_user)
    assert created["missing_fields"] == []
    assert created["opening_line"] == (
        "Hi, I'm an AI assistant calling on behalf of Alex. This call is transcribed. "
        "Book a table for 4 under Alex."
    )
    assert created["brief"] == example_brief()

    read = client.get(f"/briefs/{created['brief_id']}", headers=auth(signed_up_user))
    assert read.status_code == 200
    assert read.json() == created

    with psycopg.connect(supabase_env.db_url) as conn:
        row = conn.execute(
            "select user_id, goal, phone_e164, template_key, missing_fields from public.briefs"
            " where id = %s",
            (created["brief_id"],),
        ).fetchone()
    assert row == (
        signed_up_user.user_id,
        "Book a table for 4",
        "+14155550123",
        "restaurant_booking",
        [],
    )


def test_another_user_can_neither_read_nor_replace_the_brief(
    client: TestClient, supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    alex, bob = signed_up_user, sign_up(supabase_env)
    set_profile(supabase_env, alex.user_id, ALEX)
    created = create(client, alex)
    url = f"/briefs/{created['brief_id']}"

    read = client.get(url, headers=auth(bob))
    assert read.status_code == 404
    assert read.json()["error"]["code"] == "brief_not_found"

    replaced = client.patch(
        url, json=example_brief(goal="Bob was here"), headers=auth(bob, uuid.uuid4())
    )
    assert replaced.status_code == 404
    assert replaced.json()["error"]["code"] == "brief_not_found"

    # Alex's brief is untouched, and Bob's 404 looks exactly like a brief that doesn't exist.
    assert client.get(url, headers=auth(alex)).json()["brief"]["goal"] == "Book a table for 4"
    missing = client.get(f"/briefs/{uuid.uuid4()}", headers=auth(bob))
    assert missing.json() == read.json()

    async def readiness_for_bob() -> object:
        async with await psycopg.AsyncConnection.connect(supabase_env.db_url) as conn:
            return await brief_readiness(conn, bob.user_id, uuid.UUID(created["brief_id"]))

    assert asyncio.run(readiness_for_bob()) is None


def test_create_replays_with_the_same_idempotency_key(
    client: TestClient, supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    set_profile(supabase_env, signed_up_user.user_id, ALEX)
    key = uuid.uuid4()
    first = client.post("/briefs", json=example_brief(), headers=auth(signed_up_user, key))
    again = client.post("/briefs", json=example_brief(), headers=auth(signed_up_user, key))
    assert first.status_code == again.status_code == 201
    assert again.json() == first.json()
    with psycopg.connect(supabase_env.db_url) as conn:
        count = conn.execute(
            "select count(*) from public.briefs where user_id = %s", (signed_up_user.user_id,)
        ).fetchone()
    assert count == (1,)


def test_replace_after_review_then_refuse_once_a_call_started(
    client: TestClient, supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    set_profile(supabase_env, signed_up_user.user_id, ALEX)
    created = create(client, signed_up_user)
    url = f"/briefs/{created['brief_id']}"
    edited = example_brief(fallbacks=[{"text": "Saturday same time"}, {"text": "Friday 8:30pm"}])

    replaced = client.patch(url, json=edited, headers=auth(signed_up_user, uuid.uuid4()))
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["brief"]["fallbacks"] == edited["fallbacks"]
    assert replaced.json()["created_at"] == created["created_at"]
    assert client.get(url, headers=auth(signed_up_user)).json()["brief"] == edited

    with psycopg.connect(supabase_env.db_url, autocommit=True) as conn:
        conn.execute(
            "insert into public.calls (user_id, brief_id, status) values (%s, %s, 'dialing')",
            (signed_up_user.user_id, created["brief_id"]),
        )
    refused = client.patch(url, json=example_brief(), headers=auth(signed_up_user, uuid.uuid4()))
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "brief_in_use"


def test_readiness_follows_the_profile_as_it_is_now(
    client: TestClient, supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    """No calling name: Start is blocked. Once the profile has one, the same brief is ready."""
    created = create(client, signed_up_user)
    assert created["missing_fields"] == ["calling_name"]
    assert created["opening_line"] is None

    set_profile(supabase_env, signed_up_user.user_id, ALEX)
    read = client.get(f"/briefs/{created['brief_id']}", headers=auth(signed_up_user)).json()
    assert read["missing_fields"] == []
    assert read["opening_line"] is not None
