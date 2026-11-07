"""Tests for the call event log (append_event) and the call-lock helpers (calls_lock)."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, NoReturn

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from app.calls_lock import CallNotFound, end_call_transition, lock_call, write_call_secrets
from app.events import ContractError, append_event
from app.secrets_registry import SecretRegistry, get_secret_registry
from tests.conftest import FreshCall, SupabaseEnv, create_fresh_call

NOW = datetime(2026, 11, 7, 18, 0, 0, 123000, tzinfo=UTC)


class _NoDatabase:
    """A stand-in connection that fails the test if append_event touches the database."""

    def __getattr__(self, name: str) -> NoReturn:
        raise AssertionError(f"append_event used the database ({name}) before validating")


async def _with_pool(db_url: str, body: Callable[[AsyncConnectionPool], Awaitable[None]]) -> None:
    async with AsyncConnectionPool(db_url, min_size=2, max_size=10, open=False) as pool:
        await body(pool)


async def _fetch(db_url: str, sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    async with await psycopg.AsyncConnection.connect(db_url, autocommit=True) as conn:
        return await (await conn.execute(sql, params)).fetchall()


async def _seed_secrets_ping_outbox(db_url: str, call_id: uuid.UUID) -> uuid.UUID:
    """Give the call live secrets, an open ping and pending control + notify outbox rows."""
    ping_id = uuid.uuid4()
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        async with conn.transaction():
            assert await write_call_secrets(
                conn,
                call_id,
                f"https://control.example.test/{call_id}",
                f"wss://listen.example.test/{call_id}",
            )
            await conn.execute(
                "insert into public.pings (ping_id, call_id, kind, question, vapi_tool_call_id,"
                " source_ts) values (%s, %s, 'decision', 'Is 7pm okay?', %s, now())",
                (ping_id, call_id, f"tool-{ping_id}"),
            )
            await conn.execute(
                "insert into public.outbox (call_id, lane, kind) values"
                " (%s, 'control', 'say'), (%s, 'notify', 'push')",
                (call_id, call_id),
            )
    return ping_id


# --- append_event: validation (no database needed) -------------------------------------


def test_payload_with_score_fields_rejected() -> None:
    """DC-5: confidence and dimensions never reach a browser-readable payload."""
    payload = {
        "situation": "Offered 8pm instead of 7pm",
        "action": "accept",
        "reason": "Within the allowed window",
        "confidence": "high",
        "dimensions": {"involves_money": False},
    }
    with pytest.raises(ContractError):
        asyncio.run(
            append_event(
                _NoDatabase(),  # type: ignore[arg-type]
                call_id=uuid.uuid4(),
                type="agent.decision",
                source="backend",
                payload=payload,
                occurred_at=NOW,
            )
        )


@pytest.mark.parametrize("event_type", ["made.up", "../brief", "status.changed.v2", ""])
def test_unknown_type_rejected(event_type: str) -> None:
    with pytest.raises(ContractError):
        asyncio.run(
            append_event(
                _NoDatabase(),  # type: ignore[arg-type]
                call_id=uuid.uuid4(),
                type=event_type,
                source="backend",
                payload={"status": "ended"},
                occurred_at=NOW,
            )
        )


def test_invalid_payload_closed_enum_rejected() -> None:
    """Writers validate against the closed enum even though readers treat it as open."""
    with pytest.raises(ContractError):
        asyncio.run(
            append_event(
                _NoDatabase(),  # type: ignore[arg-type]
                call_id=uuid.uuid4(),
                type="status.changed",
                source="backend",
                payload={"status": "teleported"},
                occurred_at=NOW,
            )
        )


def test_naive_occurred_at_and_bad_source_rejected() -> None:
    for kwargs in (
        {"source": "backend", "occurred_at": datetime(2026, 11, 7, 18, 0)},
        {"source": "browser", "occurred_at": NOW},
    ):
        with pytest.raises(ContractError):
            asyncio.run(
                append_event(
                    _NoDatabase(),  # type: ignore[arg-type]
                    call_id=uuid.uuid4(),
                    type="status.changed",
                    payload={"status": "ended"},
                    **kwargs,  # type: ignore[arg-type]
                )
            )


def test_contract_error_is_value_error() -> None:
    assert issubclass(ContractError, ValueError)


def test_secret_registry_add_and_all() -> None:
    registry = SecretRegistry()
    registry.add("https://control.example.test/a")
    registry.add("https://control.example.test/a")
    registry.add("")
    assert registry.all() == frozenset({"https://control.example.test/a"})
    assert get_secret_registry() is get_secret_registry()


# --- append_event: database ------------------------------------------------------------


@pytest.mark.db
def test_append_event_returns_call_event(supabase_env: SupabaseEnv, fresh_call: FreshCall) -> None:
    async def scenario() -> None:
        async with await psycopg.AsyncConnection.connect(supabase_env.db_url) as conn:
            async with conn.transaction():
                event = await append_event(
                    conn,
                    call_id=fresh_call.call_id,
                    type="transcript.final",
                    source="vapi",
                    payload={"role": "business", "text": "Hello?", "source_start_ts": None},
                    occurred_at=NOW,
                    dedup_key="transcript:1",
                )
        assert event is not None
        assert event.seq == 1
        assert event.call_id == fresh_call.call_id
        assert event.type == "transcript.final"
        assert event.source is not None and event.source.value == "vapi"
        assert event.schema_version == 1
        assert event.occurred_at == NOW
        assert event.payload["text"] == "Hello?"
        rows = await _fetch(
            supabase_env.db_url,
            "select last_seq from public.calls where id = %s",
            (fresh_call.call_id,),
        )
        assert rows == [(1,)]

    asyncio.run(scenario())


@pytest.mark.db
def test_append_event_unknown_call_raises(supabase_env: SupabaseEnv) -> None:
    async def scenario() -> None:
        async with await psycopg.AsyncConnection.connect(supabase_env.db_url) as conn:
            with pytest.raises(CallNotFound):
                async with conn.transaction():
                    await append_event(
                        conn,
                        call_id=uuid.uuid4(),
                        type="status.changed",
                        source="backend",
                        payload={"status": "dialing"},
                        occurred_at=NOW,
                    )

    asyncio.run(scenario())


@pytest.mark.db
def test_seq_is_gap_free_under_concurrency(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def append_one(pool: AsyncConnectionPool, n: int) -> None:
        async with pool.connection() as conn:
            async with conn.transaction():
                await append_event(
                    conn,
                    call_id=fresh_call.call_id,
                    type="transcript.final",
                    source="vapi",
                    payload={"role": "agent", "text": f"line {n}", "source_start_ts": None},
                    occurred_at=NOW,
                    dedup_key=f"line:{n}",
                )

    async def body(pool: AsyncConnectionPool) -> None:
        await asyncio.gather(*(append_one(pool, n) for n in range(20)))

    asyncio.run(_with_pool(supabase_env.db_url, body))
    rows = asyncio.run(
        _fetch(
            supabase_env.db_url,
            "select seq from public.call_events where call_id = %s order by seq",
            (fresh_call.call_id,),
        )
    )
    assert [r[0] for r in rows] == list(range(1, 21))
    last = asyncio.run(
        _fetch(
            supabase_env.db_url,
            "select last_seq from public.calls where id = %s",
            (fresh_call.call_id,),
        )
    )
    assert last == [(20,)]


@pytest.mark.db
def test_duplicate_dedup_key_returns_none_and_no_gap(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def append(conn: psycopg.AsyncConnection[Any], key: str | None) -> Any:
        return await append_event(
            conn,
            call_id=fresh_call.call_id,
            type="status.changed",
            source="vapi",
            payload={"status": "on_hold"},
            occurred_at=NOW,
            dedup_key=key,
        )

    async def scenario() -> None:
        async with await psycopg.AsyncConnection.connect(supabase_env.db_url) as conn:
            async with conn.transaction():
                first = await append(conn, "status:on_hold:1")
                duplicate = await append(conn, "status:on_hold:1")
                second = await append(conn, "status:on_hold:2")
                # dedup_key=None means no dedup at all.
                none_a = await append(conn, None)
                none_b = await append(conn, None)
            assert first is not None and first.seq == 1
            assert duplicate is None
            assert second is not None and second.seq == 2
            assert none_a is not None and none_a.seq == 3
            assert none_b is not None and none_b.seq == 4
        # A duplicate in a separate, later transaction is also dropped without burning a seq.
        async with await psycopg.AsyncConnection.connect(supabase_env.db_url) as conn:
            async with conn.transaction():
                assert await append(conn, "status:on_hold:2") is None
                after = await append(conn, "status:on_hold:3")
            assert after is not None and after.seq == 5
        rows = await _fetch(
            supabase_env.db_url,
            "select seq from public.call_events where call_id = %s order by seq",
            (fresh_call.call_id,),
        )
        assert [r[0] for r in rows] == [1, 2, 3, 4, 5]

    asyncio.run(scenario())


# --- calls_lock ------------------------------------------------------------------------


@pytest.mark.db
def test_lock_call_returns_row_and_raises_when_missing(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        async with await psycopg.AsyncConnection.connect(supabase_env.db_url) as conn:
            async with conn.transaction():
                row = await lock_call(conn, fresh_call.call_id)
                assert row.id == fresh_call.call_id
                assert row.user_id == fresh_call.user_id
                assert row.status == "talking"
                assert row.ended_at is None
                assert row.vapi_call_id == f"vapi-{fresh_call.call_id}"
                assert row.last_seq == 0
            with pytest.raises(LookupError):
                async with conn.transaction():
                    await lock_call(conn, uuid.uuid4())

    asyncio.run(scenario())


@pytest.mark.db
def test_lock_helpers_refuse_autocommit_outside_transaction(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    """A FOR UPDATE lock in autocommit mode is released at once, so it would protect nothing."""

    async def scenario() -> None:
        async with await psycopg.AsyncConnection.connect(
            supabase_env.db_url, autocommit=True
        ) as conn:
            with pytest.raises(RuntimeError):
                await lock_call(conn, fresh_call.call_id)

    asyncio.run(scenario())


@pytest.mark.db
def test_write_call_secrets_registers_urls(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    control = f"https://control.example.test/{fresh_call.call_id}/v1"
    listen = f"wss://listen.example.test/{fresh_call.call_id}/v1"

    async def scenario() -> None:
        async with await psycopg.AsyncConnection.connect(supabase_env.db_url) as conn:
            async with conn.transaction():
                assert await write_call_secrets(conn, fresh_call.call_id, control, listen)
            # A second write updates the row in place.
            async with conn.transaction():
                assert await write_call_secrets(conn, fresh_call.call_id, control + "b", listen)
        rows = await _fetch(
            supabase_env.db_url,
            "select control_url, listen_url from public.call_secrets where call_id = %s",
            (fresh_call.call_id,),
        )
        assert rows == [(control + "b", listen)]

    asyncio.run(scenario())
    assert {control, control + "b", listen} <= get_secret_registry().all()


@pytest.mark.db
def test_secrets_write_after_end_is_refused(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    """A locks and ends the call while B's write_call_secrets waits; B then writes nothing."""
    call_id = fresh_call.call_id

    async def scenario() -> None:
        await _seed_secrets_ping_outbox(supabase_env.db_url, call_id)
        conn_a = await psycopg.AsyncConnection.connect(supabase_env.db_url)
        conn_b = await psycopg.AsyncConnection.connect(supabase_env.db_url)
        try:
            async with conn_a.transaction():
                await lock_call(conn_a, call_id)

                async def write_b() -> bool:
                    async with conn_b.transaction():
                        return await write_call_secrets(
                            conn_b,
                            call_id,
                            f"https://control.example.test/{call_id}/late",
                            f"wss://listen.example.test/{call_id}/late",
                        )

                task_b = asyncio.create_task(write_b())
                await asyncio.sleep(0.5)
                assert not task_b.done(), "B should be blocked on A's row lock"
                assert await end_call_transition(
                    conn_a, call_id, reason="customer-ended-call", source="vapi"
                )
            async with asyncio.timeout(10):
                assert await task_b is False
        finally:
            await conn_a.close()
            await conn_b.close()
        assert (
            await _fetch(
                supabase_env.db_url,
                "select 1 from public.call_secrets where call_id = %s",
                (call_id,),
            )
            == []
        )

    asyncio.run(scenario())


@pytest.mark.db
def test_late_create_response_does_not_restore_secrets(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    """The call ended before Vapi's create response came back: its URLs are never stored."""
    call_id = fresh_call.call_id
    control = f"https://control.example.test/{call_id}/created-late"
    listen = f"wss://listen.example.test/{call_id}/created-late"

    async def scenario() -> None:
        async with await psycopg.AsyncConnection.connect(supabase_env.db_url) as conn:
            async with conn.transaction():
                assert await end_call_transition(
                    conn, call_id, reason="end-of-call-report", source="vapi"
                )
            async with conn.transaction():
                assert await write_call_secrets(conn, call_id, control, listen) is False
        assert (
            await _fetch(
                supabase_env.db_url,
                "select 1 from public.call_secrets where call_id = %s",
                (call_id,),
            )
            == []
        )

    asyncio.run(scenario())
    assert control not in get_secret_registry().all()


@pytest.mark.db
@pytest.mark.parametrize("order", ["report_first", "status_first"])
def test_end_transition_idempotent_and_order_independent(
    supabase_env: SupabaseEnv, fresh_call: FreshCall, order: str
) -> None:
    call_id = fresh_call.call_id
    first, second = ("end-of-call-report", "status-update")
    if order == "status_first":
        first, second = second, first

    async def scenario() -> None:
        ping_id = await _seed_secrets_ping_outbox(supabase_env.db_url, call_id)
        async with await psycopg.AsyncConnection.connect(supabase_env.db_url) as conn:
            async with conn.transaction():
                assert await end_call_transition(conn, call_id, reason=first, source="vapi")
            async with conn.transaction():
                assert not await end_call_transition(conn, call_id, reason=second, source="vapi")

        db = supabase_env.db_url
        events = await _fetch(
            db,
            "select type, source, payload, dedup_key from public.call_events where call_id = %s",
            (call_id,),
        )
        assert events == [("status.changed", "vapi", {"status": "ended"}, None)]
        call = await _fetch(
            db, "select status, ended_at is not null from public.calls where id = %s", (call_id,)
        )
        assert call == [("ended", True)]
        secrets = await _fetch(
            db, "select 1 from public.call_secrets where call_id = %s", (call_id,)
        )
        assert secrets == []
        pings = await _fetch(db, "select state from public.pings where ping_id = %s", (ping_id,))
        assert pings == [("cancelled",)]
        outbox = await _fetch(
            db, "select lane, state from public.outbox where call_id = %s order by lane", (call_id,)
        )
        assert outbox == [("control", "cancelled"), ("notify", "pending")]

    asyncio.run(scenario())


@pytest.mark.db
def test_end_vs_reply_no_deadlock(supabase_env: SupabaseEnv) -> None:
    """end_call_transition racing a user's ping reply never deadlocks and stays consistent."""

    async def reply(pool: AsyncConnectionPool, call_id: uuid.UUID, ping_id: uuid.UUID) -> None:
        async with pool.connection() as conn:
            async with conn.transaction():
                await lock_call(conn, call_id)
                answered = await (
                    await conn.execute(
                        "update public.pings set state = 'answered', answered_at = now()"
                        " where ping_id = %s and state = 'open' returning ping_id",
                        (ping_id,),
                    )
                ).fetchone()
                if answered is not None:
                    await append_event(
                        conn,
                        call_id=call_id,
                        type="ping.resolved",
                        source="user",
                        payload={"ping_id": str(ping_id), "state": "answered", "reply_text": "Yes"},
                        occurred_at=datetime.now(UTC),
                    )

    async def end(pool: AsyncConnectionPool, call_id: uuid.UUID) -> None:
        async with pool.connection() as conn:
            async with conn.transaction():
                assert await end_call_transition(
                    conn, call_id, reason="end-of-call-report", source="vapi"
                )

    async def body(pool: AsyncConnectionPool) -> None:
        outcomes: set[str] = set()
        for _ in range(20):
            call = create_fresh_call(supabase_env.db_url)
            ping_id = await _seed_secrets_ping_outbox(supabase_env.db_url, call.call_id)
            async with asyncio.timeout(10):
                await asyncio.gather(end(pool, call.call_id), reply(pool, call.call_id, ping_id))
            db = supabase_env.db_url
            ping = await _fetch(db, "select state from public.pings where ping_id=%s", (ping_id,))
            events = await _fetch(
                db,
                "select seq, type from public.call_events where call_id = %s order by seq",
                (call.call_id,),
            )
            state = ping[0][0]
            outcomes.add(state)
            if state == "answered":
                # The reply committed first; the call was ended afterwards.
                assert events == [(1, "ping.resolved"), (2, "status.changed")]
            else:
                assert state == "cancelled"
                assert events == [(1, "status.changed")]
            ended = await _fetch(
                db, "select status from public.calls where id = %s", (call.call_id,)
            )
            assert ended == [("ended",)]

    asyncio.run(_with_pool(supabase_env.db_url, body))
