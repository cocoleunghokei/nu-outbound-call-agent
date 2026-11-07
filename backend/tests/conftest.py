"""Shared fixtures. db tests need real Postgres and Supabase Auth and fail loudly without."""

import os
import subprocess
import uuid
from dataclasses import dataclass

import httpx
import psycopg
import pytest


@dataclass(frozen=True)
class SupabaseEnv:
    """Where the local Postgres and Auth API live."""

    db_url: str
    auth_url: str  # Auth API root, e.g. http://127.0.0.1:54321/auth/v1
    jwt_secret: str
    jwt_issuer: str


def _status_env() -> dict[str, str]:
    """Parse `supabase status -o env`; raise a clear error if it cannot run."""
    try:
        out = subprocess.run(
            ["supabase", "status", "-o", "env"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            "db tests need SUPABASE_DB_URL, SUPABASE_AUTH_URL, SUPABASE_JWT_SECRET and "
            "SUPABASE_JWT_ISSUER, or a running `supabase start`; neither was found"
        ) from exc
    values: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            name, _, raw = line.partition("=")
            values[name.strip()] = raw.strip().strip('"')
    return values


@pytest.fixture(scope="session")
def supabase_env() -> SupabaseEnv:
    """Read the environment variables, falling back to `supabase status -o env`."""
    names = ("SUPABASE_DB_URL", "SUPABASE_AUTH_URL", "SUPABASE_JWT_SECRET", "SUPABASE_JWT_ISSUER")
    env = {name: os.environ.get(name, "") for name in names}
    if not all(env.values()):
        status = _status_env()
        api_url = status.get("API_URL", "").rstrip("/")
        env["SUPABASE_DB_URL"] = env["SUPABASE_DB_URL"] or status.get("DB_URL", "")
        env["SUPABASE_AUTH_URL"] = env["SUPABASE_AUTH_URL"] or f"{api_url}/auth/v1"
        env["SUPABASE_JWT_SECRET"] = env["SUPABASE_JWT_SECRET"] or status.get("JWT_SECRET", "")
        env["SUPABASE_JWT_ISSUER"] = env["SUPABASE_JWT_ISSUER"] or f"{api_url}/auth/v1"
    missing = [name for name, value in env.items() if not value]
    if missing:
        raise RuntimeError(f"db tests cannot find: {', '.join(missing)}")
    return SupabaseEnv(
        db_url=env["SUPABASE_DB_URL"],
        auth_url=env["SUPABASE_AUTH_URL"].rstrip("/"),
        jwt_secret=env["SUPABASE_JWT_SECRET"],
        jwt_issuer=env["SUPABASE_JWT_ISSUER"],
    )


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
