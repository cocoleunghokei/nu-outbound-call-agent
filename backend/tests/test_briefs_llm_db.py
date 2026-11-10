"""POST /briefs/interpret and the LLM rate limit against the local Supabase stack (make test-db).

The LLM is always a fake (dependency override); the rate limit, profile and isolation are real
Postgres. 10 LLM builds per user per rolling hour; only builds that reach the LLM count.
"""

import asyncio
import json
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.briefs.llm import GoalInterpretation, LlmUnavailable
from app.briefs.ratelimit import LLM_BUILDS_PER_HOUR, reserve_llm_build
from app.briefs.router import get_goal_interpreter
from app.config import Settings
from app.errors import ApiError
from app.main import create_app
from tests.conftest import SignedUpUser, SupabaseEnv
from tests.test_briefs_builder import body
from tests.test_briefs_db import ALEX, auth, set_profile, sign_up

pytestmark = pytest.mark.db


class FakeLlm:
    """Answers with a fixed summary, or raises ``fail`` once per queued error."""

    def __init__(self) -> None:
        self.goals: list[str] = []
        self.fail: list[Exception] = []

    async def interpret_goal(self, goal: str) -> GoalInterpretation:
        self.goals.append(goal)
        if self.fail:
            raise self.fail.pop(0)
        return GoalInterpretation(
            goal_summary="ask if a cake can be held for pickup tomorrow",
            suggested_constraints=["Pickup tomorrow"],
        )


@pytest.fixture
def llm() -> FakeLlm:
    return FakeLlm()


def _app(env: SupabaseEnv) -> Any:
    settings = Settings(
        database_url=env.db_url,
        supabase_jwt_secret=env.jwt_secret,  # type: ignore[arg-type]
        supabase_jwt_issuer=env.jwt_issuer,
        supabase_jwks_url=f"{env.auth_url}/.well-known/jwks.json",
    )
    return create_app(settings)


@pytest.fixture
def client(supabase_env: SupabaseEnv, llm: FakeLlm) -> Iterator[TestClient]:
    app = _app(supabase_env)
    app.dependency_overrides[get_goal_interpreter] = lambda: llm
    with TestClient(app) as test_client:
        yield test_client


CUSTOM = body(
    template_key=None,
    goal="Can you check if they can hold a cake for pickup tomorrow? DOB 2004-03-15",
    business_name="Sunrise Bakery",
    phone_e164="617 555 0122",
    answers={},
    constraints=[],
)


def interpret(client: TestClient, user: SignedUpUser, data: dict[str, Any] = CUSTOM) -> Any:
    return client.post("/briefs/interpret", json=data, headers=auth(user))


def build_rows(env: SupabaseEnv, user_id: uuid.UUID) -> int:
    with psycopg.connect(env.db_url, autocommit=True) as conn:
        row = conn.execute(
            "select count(*) from public.brief_builds where user_id = %s", (user_id,)
        ).fetchone()
    assert row is not None
    return int(row[0])


def test_interpret_fills_the_summary_from_the_llm(
    client: TestClient, llm: FakeLlm, supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    set_profile(supabase_env, signed_up_user.user_id, ALEX)
    data = {**CUSTOM, "goal": "Can you check if they can hold a cake for pickup tomorrow?"}
    response = interpret(client, signed_up_user, data)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["interpretation"]["llm_used"] is True
    assert result["interpretation"]["suggested_constraints"] == ["Pickup tomorrow"]
    assert result["brief"]["task_vars"] == {
        "goal_summary": {"literal": "ask if a cake can be held for pickup tomorrow"}
    }
    assert result["brief"]["constraints"] == []  # suggestions are offers, not brief content
    assert result["missing_fields"] == []
    assert result["opening_line"].endswith(
        "I'm calling to ask if a cake can be held for pickup tomorrow."
    )
    assert build_rows(supabase_env, signed_up_user.user_id) == 1


def test_the_profile_never_reaches_the_llm(
    client: TestClient, llm: FakeLlm, supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    set_profile(supabase_env, signed_up_user.user_id, ALEX)
    result = interpret(client, signed_up_user).json()
    (sent,) = llm.goals
    assert "hold a cake" in sent
    for value, _ in ALEX.values():
        assert value not in sent
    # The typed Ask-me-first DOB stays in the user's own goal, so Start is blocked.
    assert "date_of_birth" in result["missing_fields"]
    assert result["opening_line"] is None


def test_a_timeout_is_retryable_and_the_retry_works(
    client: TestClient, llm: FakeLlm, supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    set_profile(supabase_env, signed_up_user.user_id, ALEX)
    llm.fail.append(LlmUnavailable("timeout"))
    first = interpret(client, signed_up_user)
    assert first.status_code == 503
    assert first.json()["error"]["code"] == "llm_unavailable"
    assert first.json()["error"]["retryable"] is True
    second = interpret(client, signed_up_user)
    assert second.status_code == 200, second.text
    # Both reached the LLM, so both count.
    assert build_rows(supabase_env, signed_up_user.user_id) == 2


def test_without_a_key_custom_builds_use_the_users_words_and_dont_count(
    supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    set_profile(supabase_env, signed_up_user.user_id, ALEX)
    with TestClient(_app(supabase_env)) as no_key_client:  # default settings: no key
        for _ in range(LLM_BUILDS_PER_HOUR + 1):
            response = interpret(no_key_client, signed_up_user)
            assert response.status_code == 200, response.text
    result = response.json()
    assert result["interpretation"]["llm_used"] is False
    assert result["interpretation"]["reason"] == "llm_not_configured"
    assert result["brief"]["task_vars"]["goal_summary"]["literal"].startswith(
        "can you check if they can hold a cake"
    )
    assert build_rows(supabase_env, signed_up_user.user_id) == 0


def test_ten_llm_builds_per_hour_then_429(
    client: TestClient, llm: FakeLlm, supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    for n in range(LLM_BUILDS_PER_HOUR):
        response = interpret(client, signed_up_user)
        assert response.status_code == 200, (n, response.text)
    over = interpret(client, signed_up_user)
    assert over.status_code == 429
    error = over.json()["error"]
    assert error["code"] == "rate_limited"
    assert error["retryable"] is True
    assert "10 times in the last hour" in error["message"]
    assert len(llm.goals) == LLM_BUILDS_PER_HOUR  # the 11th never reached the LLM
    # The plain builder still works for this user.
    rebuilt = client.post(
        "/briefs/build",
        json={**CUSTOM, "goal_summary": "ask if a cake can be held"},
        headers=auth(signed_up_user),
    )
    assert rebuilt.status_code == 200, rebuilt.text


def test_other_users_are_unaffected(
    client: TestClient, supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    for _ in range(LLM_BUILDS_PER_HOUR):
        assert interpret(client, signed_up_user).status_code == 200
    assert interpret(client, signed_up_user).status_code == 429
    bob = sign_up(supabase_env)
    assert interpret(client, bob).status_code == 200


def test_template_builds_and_refused_calls_are_not_counted(
    client: TestClient, llm: FakeLlm, supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    for _ in range(LLM_BUILDS_PER_HOUR + 1):
        assert (
            client.post("/briefs/build", json=body(), headers=auth(signed_up_user)).status_code
            == 200
        )
        assert interpret(client, signed_up_user, body()).status_code == 422
    assert build_rows(supabase_env, signed_up_user.user_id) == 0
    assert llm.goals == []
    assert interpret(client, signed_up_user).status_code == 200


def test_builds_older_than_an_hour_free_their_slot(
    client: TestClient, supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    with psycopg.connect(supabase_env.db_url, autocommit=True) as conn:
        conn.execute(
            "insert into public.profiles (id) values (%s) on conflict do nothing",
            (signed_up_user.user_id,),
        )
        for _ in range(LLM_BUILDS_PER_HOUR):
            conn.execute(
                "insert into public.brief_builds (user_id, created_at)"
                " values (%s, now() - interval '61 minutes')",
                (signed_up_user.user_id,),
            )
    assert interpret(client, signed_up_user).status_code == 200
    assert build_rows(supabase_env, signed_up_user.user_id) == 1  # old rows deleted


def test_parallel_reservations_never_pass_the_limit(
    supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    """The advisory lock makes count-then-insert race-safe: 15 at once, exactly 10 win."""
    user_id = signed_up_user.user_id

    async def one() -> bool:
        async with await psycopg.AsyncConnection.connect(supabase_env.db_url) as conn:
            try:
                async with conn.transaction():
                    await reserve_llm_build(conn, user_id)
            except ApiError as exc:
                assert exc.code == "rate_limited"
                return False
            return True

    async def all_at_once() -> list[bool]:
        return await asyncio.gather(*(one() for _ in range(15)))

    results = asyncio.run(all_at_once())
    assert results.count(True) == LLM_BUILDS_PER_HOUR
    assert build_rows(supabase_env, user_id) == LLM_BUILDS_PER_HOUR


def test_the_table_is_backend_only(supabase_env: SupabaseEnv) -> None:
    with psycopg.connect(supabase_env.db_url, autocommit=True) as conn:
        rls = conn.execute(
            "select relrowsecurity from pg_class where oid = 'public.brief_builds'::regclass"
        ).fetchone()
        grants = conn.execute(
            "select grantee, privilege_type from information_schema.role_table_grants"
            " where table_schema = 'public' and table_name = 'brief_builds'"
            " and grantee in ('anon', 'authenticated')"
        ).fetchall()
    assert rls == (True,)
    assert grants == [], json.dumps(grants)
