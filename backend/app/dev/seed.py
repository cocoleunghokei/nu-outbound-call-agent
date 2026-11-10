"""Seed a local user, profile, brief and call so the team can develop without Vapi.

Usage: ``uv run python -m app.dev.seed [--email E] [--vapi-call-id ID]``. Prints
``CALL_ID=`` and ``ACCESS_TOKEN=`` lines. Reads ``SUPABASE_DB_URL`` and ``SUPABASE_AUTH_URL``,
falling back to ``supabase status -o env``. It refuses any database that is not on this
machine, and re-running it resets that user's seeded call.
"""

import argparse
import sys
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import psycopg

from app.dev.local_stack import discover_supabase_env

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
DEV_PASSWORD = "correct-horse-battery"  # fake local-only password
# A fake DOB, marked ask_first so it must never reach a prompt.
PROFILE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("calling_name", "Alex", "share_freely"),
    ("full_name", "Alex Chen", "share_freely"),
    ("date_of_birth", "1990-01-02", "ask_first"),
)


class NotLocalDatabase(RuntimeError):
    """The database URL does not point at this machine."""


@dataclass(frozen=True)
class SeededCall:
    """What seed_dev created."""

    call_id: uuid.UUID
    user_id: uuid.UUID
    access_token: str


def assert_local_db(db_url: str) -> None:
    """Raise NotLocalDatabase unless the URL's host is exactly localhost or 127.0.0.1."""
    host = urlparse(db_url).hostname or ""
    if host not in LOCAL_HOSTS:
        raise NotLocalDatabase(
            f"refusing to seed: database host {host!r} is not localhost or 127.0.0.1"
        )


def _sign_up_or_in(auth_url: str, email: str) -> tuple[uuid.UUID, str]:
    """Create the user through the Auth API, or sign in if it already exists."""
    credentials = {"email": email, "password": DEV_PASSWORD}
    response = httpx.post(f"{auth_url}/signup", json=credentials, timeout=15)
    if response.status_code != 200 or "access_token" not in response.json():
        response = httpx.post(
            f"{auth_url}/token",
            params={"grant_type": "password"},
            json=credentials,
            timeout=15,
        )
    if response.status_code != 200:
        raise RuntimeError(f"auth sign-up and sign-in both failed: {response.status_code}")
    body = response.json()
    return uuid.UUID(body["user"]["id"]), str(body["access_token"])


def seed_dev(
    db_url: str, auth_url: str, email: str = "alex@example.test", vapi_call_id: str = "fake-1"
) -> SeededCall:
    """Create (or reset) the user's profile, one brief and one 'talking' call."""
    assert_local_db(db_url)
    user_id, token = _sign_up_or_in(auth_url.rstrip("/"), email)
    call_id, brief_id = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(db_url) as conn:  # one transaction; the connection commits on exit
        conn.execute(
            "insert into public.profiles (id) values (%s) on conflict do nothing", (user_id,)
        )
        for key, value, sharing in PROFILE_FIELDS:
            conn.execute(
                "insert into public.profile_fields (user_id, key, value, sharing)"
                " values (%s, %s, %s, %s)"
                " on conflict (user_id, key) do update set value = excluded.value,"
                " sharing = excluded.sharing",
                (user_id, key, value, sharing),
            )
        # Reset: deleting the old call cascades to its events, pings and receipts.
        conn.execute("delete from public.calls where vapi_call_id = %s", (vapi_call_id,))
        conn.execute(
            "insert into public.briefs (id, user_id, goal, business_name, phone_e164, brief)"
            " values (%s, %s, 'Book a table for two', 'Alex Chen Bistro', '+15555550123', '{}')",
            (brief_id, user_id),
        )
        conn.execute(
            "insert into public.calls (id, user_id, brief_id, status, vapi_call_id)"
            " values (%s, %s, %s, 'talking', %s)",
            (call_id, user_id, brief_id, vapi_call_id),
        )
    return SeededCall(call_id=call_id, user_id=user_id, access_token=token)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: seed and print CALL_ID and ACCESS_TOKEN."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default="alex@example.test")
    parser.add_argument("--vapi-call-id", default="fake-1")
    args = parser.parse_args(argv)
    env = discover_supabase_env()
    try:
        seeded = seed_dev(env.db_url, env.auth_url, args.email, args.vapi_call_id)
    except NotLocalDatabase as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"CALL_ID={seeded.call_id}")
    print(f"ACCESS_TOKEN={seeded.access_token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
