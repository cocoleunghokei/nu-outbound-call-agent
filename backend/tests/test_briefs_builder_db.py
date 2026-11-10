"""POST /briefs/build against the local Supabase stack (make test-db; CI runs it).

The backend is the database owner, so RLS doesn't apply: these prove the build reads only the
caller's own profile, and that profile additions are never written to profile_fields.
"""

import json
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.conftest import SignedUpUser, SupabaseEnv
from tests.test_briefs_builder import body
from tests.test_briefs_db import auth, client, set_profile, sign_up

__all__ = ["client"]  # the fixture, re-exported so pytest finds it here

pytestmark = pytest.mark.db

ALEX = {
    "calling_name": ("Alex", "share_freely"),
    "phone": ("+16175550142", "share_freely"),
    "date_of_birth": ("2004-03-15", "ask_first"),
}
BOB = {
    "calling_name": ("Bob", "share_freely"),
    "phone": ("+16175550177", "share_freely"),
    "date_of_birth": ("1999-12-31", "ask_first"),
}

DOCTOR_BODY = body(
    template_key="doctor_booking",
    goal="Doctor: book an appointment",
    business_name="Back Bay Family Clinic",
    phone_e164="617 555 0111",
    answers={"provider_name": "Dr. Lee", "patient_status": "Existing", "reason": "Check-up"},
)


def build(client: TestClient, user: SignedUpUser, data: dict[str, Any]) -> Any:
    response = client.post("/briefs/build", json=data, headers=auth(user))
    assert response.status_code == 200, response.text
    return response.json()


def test_build_reads_the_callers_profile(
    client: TestClient, supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    set_profile(supabase_env, signed_up_user.user_id, ALEX)
    result = build(client, signed_up_user, DOCTOR_BODY)
    assert result["missing_fields"] == []
    assert result["opening_line"] == (
        "Hi, I'm an AI assistant calling on behalf of Alex. This call is transcribed. "
        "I'd like to book an appointment with Dr. Lee."
    )
    brief = result["brief"]
    assert brief["phone_e164"] == "+16175550111"
    assert brief["share_freely_keys"] == ["calling_name", "phone"]
    assert brief["ask_first_keys"] == ["date_of_birth"]
    assert "2004-03-15" not in json.dumps(brief)


def test_build_never_reads_another_users_profile(
    client: TestClient, supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    alex, bob = signed_up_user, sign_up(supabase_env)
    set_profile(supabase_env, bob.user_id, BOB)  # Alex has no profile at all
    result = build(client, alex, DOCTOR_BODY)
    assert result["missing_fields"] == ["calling_name", "date_of_birth"]
    assert result["opening_line"] is None
    assert result["brief"]["share_freely_keys"] == []
    assert "Bob" not in json.dumps(result)


def test_profile_additions_count_for_the_build_but_are_never_saved(
    client: TestClient, supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    set_profile(supabase_env, signed_up_user.user_id, {"calling_name": ("Alex", "share_freely")})
    data = {**DOCTOR_BODY, "profile_additions": [{"key": "date_of_birth", "value": "2004-03-15"}]}
    result = build(client, signed_up_user, data)
    assert result["missing_fields"] == []
    assert result["brief"]["ask_first_keys"] == ["date_of_birth"]
    assert "2004-03-15" not in json.dumps(result)
    with psycopg.connect(supabase_env.db_url) as conn:
        rows = conn.execute(
            "select key from public.profile_fields where user_id = %s order by key",
            (signed_up_user.user_id,),
        ).fetchall()
    assert rows == [("calling_name",)]


def test_build_writes_no_brief(
    client: TestClient, supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    set_profile(supabase_env, signed_up_user.user_id, ALEX)
    build(client, signed_up_user, DOCTOR_BODY)
    with psycopg.connect(supabase_env.db_url) as conn:
        row = conn.execute(
            "select count(*) from public.briefs where user_id = %s", (signed_up_user.user_id,)
        ).fetchone()
    assert row == (0,)
