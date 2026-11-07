"""Shared fixtures. db tests need real Postgres and Supabase Auth and fail loudly without."""

import uuid
from dataclasses import dataclass

import httpx
import psycopg
import pytest

from app.dev.local_stack import SupabaseEnv, discover_supabase_env


@pytest.fixture(scope="session")
def supabase_env() -> SupabaseEnv:
    """Read the environment variables, falling back to `supabase status -o env`."""
    return discover_supabase_env()


@dataclass(frozen=True)
class SignedUpUser:
    """A real user created through the local Auth API."""

    user_id: uuid.UUID
    access_token: str


@pytest.fixture
def signed_up_user(supabase_env: SupabaseEnv) -> SignedUpUser:
    """Sign up a fresh fake user through POST {auth_url}/signup and return its access token."""
    response = httpx.post(
        f"{supabase_env.auth_url}/signup",
        json={"email": f"alex+{uuid.uuid4()}@example.test", "password": "correct-horse-battery"},
        timeout=15,
    )
    assert response.status_code == 200, f"signup failed: {response.status_code} {response.text}"
    body = response.json()
    return SignedUpUser(
        user_id=uuid.UUID(body["user"]["id"]),
        access_token=body["access_token"],
    )


@dataclass(frozen=True)
class FreshCall:
    """A fake user, profile, brief and call row created through the owner connection."""

    call_id: uuid.UUID
    user_id: uuid.UUID
    brief_id: uuid.UUID


def create_fresh_call(db_url: str) -> FreshCall:
    """Insert a new fake auth user, profile, brief and call (status 'talking') with unique ids."""
    user_id, brief_id, call_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(
            "insert into auth.users (id, email, aud, role) values (%s, %s, 'authenticated', "
            "'authenticated')",
            (user_id, f"alex+{user_id}@example.test"),
        )
        conn.execute("insert into public.profiles (id) values (%s)", (user_id,))
        conn.execute(
            "insert into public.briefs (id, user_id, goal, business_name, phone_e164, brief)"
            " values (%s, %s, 'Book a table for two', 'Alex Chen Bistro', '+15555550123', '{}')",
            (brief_id, user_id),
        )
        conn.execute(
            "insert into public.calls (id, user_id, brief_id, status, vapi_call_id)"
            " values (%s, %s, %s, 'talking', %s)",
            (call_id, user_id, brief_id, f"vapi-{call_id}"),
        )
    return FreshCall(call_id=call_id, user_id=user_id, brief_id=brief_id)


@pytest.fixture
def fresh_call(supabase_env: SupabaseEnv) -> FreshCall:
    """A fresh fake call owned by a fresh fake user; see create_fresh_call."""
    return create_fresh_call(supabase_env.db_url)
