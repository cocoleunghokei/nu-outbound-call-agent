"""Tests for idempotency keys against a real Postgres."""

import asyncio
import uuid

import psycopg
import pytest

from app.errors import ApiError
from app.idempotency import StoredResponse, claim_idempotency, store_idempotent_response
from tests.conftest import SignedUpUser, SupabaseEnv

pytestmark = pytest.mark.db


async def _ensure_profile(db_url: str, user_id: uuid.UUID) -> None:
    async with await psycopg.AsyncConnection.connect(db_url, autocommit=True) as conn:
        await conn.execute(
            "insert into public.profiles (id) values (%s) on conflict do nothing", (user_id,)
        )


def test_idempotency_replay_returns_stored(
    supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    user_id, key = signed_up_user.user_id, uuid.uuid4()

    async def scenario() -> None:
        await _ensure_profile(supabase_env.db_url, user_id)
        async with await psycopg.AsyncConnection.connect(supabase_env.db_url) as conn:
            async with conn.transaction():
                assert await claim_idempotency(conn, user_id, key) is None
                await store_idempotent_response(conn, user_id, key, 201, {"call_id": "abc"})
        async with await psycopg.AsyncConnection.connect(supabase_env.db_url) as conn:
            async with conn.transaction():
                replay = await claim_idempotency(conn, user_id, key)
        assert replay == StoredResponse(status_code=201, body={"call_id": "abc"})

    asyncio.run(scenario())


def test_idempotency_concurrent_returns_409(
    supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    user_id, key = signed_up_user.user_id, uuid.uuid4()

    async def scenario() -> None:
        await _ensure_profile(supabase_env.db_url, user_id)
        conn_a = await psycopg.AsyncConnection.connect(supabase_env.db_url)
        conn_b = await psycopg.AsyncConnection.connect(supabase_env.db_url)
        try:
            tx_a = conn_a.transaction()
            await tx_a.__aenter__()
            assert await claim_idempotency(conn_a, user_id, key) is None
            # A still holds the claim in an open transaction.
            async with asyncio.timeout(5):
                with pytest.raises(ApiError) as excinfo:
                    async with conn_b.transaction():
                        await claim_idempotency(conn_b, user_id, key)
            assert excinfo.value.code == "in_progress"
            assert excinfo.value.status == 409
            assert excinfo.value.retryable is True
            await tx_a.__aexit__(None, None, None)
        finally:
            await conn_a.close()
            await conn_b.close()

    asyncio.run(scenario())


def test_claim_without_stored_response_is_reclaimable(
    supabase_env: SupabaseEnv, signed_up_user: SignedUpUser
) -> None:
    """A claim that committed without a response (crash) lets the retry run again."""
    user_id, key = signed_up_user.user_id, uuid.uuid4()

    async def scenario() -> None:
        await _ensure_profile(supabase_env.db_url, user_id)
        async with await psycopg.AsyncConnection.connect(supabase_env.db_url) as conn:
            async with conn.transaction():
                assert await claim_idempotency(conn, user_id, key) is None
            async with conn.transaction():
                assert await claim_idempotency(conn, user_id, key) is None
                row = await (
                    await conn.execute(
                        "select response from public.idempotency_keys"
                        " where user_id = %s and key = %s",
                        (user_id, str(key)),
                    )
                ).fetchone()
                assert row == (None,)

    asyncio.run(scenario())
