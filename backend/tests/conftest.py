"""Shared fixtures. db tests need real Postgres and Supabase Auth and fail loudly without."""

import os
import subprocess
import uuid
from dataclasses import dataclass

import httpx
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
