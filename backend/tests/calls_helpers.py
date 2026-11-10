"""Helpers for the app/calls tests: a call with a profile snapshot and a control URL, an app
whose background workers are off (tests drive the sweeper and outbox by hand), signed tokens,
and ask_user webhook bodies. All data is fake."""

import time
import uuid
from datetime import UTC, datetime
from typing import Any

import jwt
import psycopg
from fastapi import FastAPI

from app.main import create_app
from tests.conftest import FreshCall, create_fresh_call
from tests.test_webhooks import make_settings, registries, rows, tool_body

# Low-entropy on purpose: a throwaway key for test tokens only.
JWT_KEY = "calls-tests-" + "k" * 32
JWT_ISSUER = "http://issuer.example.test/auth/v1"
CONTROL_URL_PREFIX = "https://control.example.test/v1/calls/"
FAKE_DOB = "March 4th, 2004"


def seed_call(db_url: str, *, status: str = "talking", calling_name: str = "Alex") -> FreshCall:
    """A fresh call with a calling name, an Ask-me-first date of birth, a share-freely email
    and a live control URL."""
    call = create_fresh_call(db_url)
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("update public.calls set status = %s where id = %s", (status, call.call_id))
        for key, value, sharing in (
            ("calling_name", calling_name, "share_freely"),
            ("date_of_birth", FAKE_DOB, "ask_first"),
            ("email", "alex@example.test", "share_freely"),
        ):
            conn.execute(
                "insert into public.call_profile_snapshots (call_id, key, value, sharing)"
                " values (%s, %s, %s, %s)",
                (call.call_id, key, value, sharing),
            )
        conn.execute(
            "insert into public.call_secrets (call_id, control_url, listen_url)"
            " values (%s, %s, %s)",
            (
                call.call_id,
                f"{CONTROL_URL_PREFIX}{call.call_id}/control/cap{uuid.uuid4().hex}",
                f"wss://listen.example.test/{call.call_id}",
            ),
        )
    return call


def make_app(db_url: str) -> FastAPI:
    """The real app (app/calls included) with its background workers switched off."""
    app = create_app(
        make_settings(db_url, supabase_jwt_secret=JWT_KEY, supabase_jwt_issuer=JWT_ISSUER)
    )
    registries(app).workers.clear()
    return app


def auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    """A signed access token for ``user_id`` and a fresh Idempotency-Key."""
    token = jwt.encode(
        {"sub": str(user_id), "aud": "authenticated", "iss": JWT_ISSUER, "exp": time.time() + 300},
        JWT_KEY,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}


def ask_body(
    call: FreshCall,
    *,
    question: str = "They offered Thu 4pm. Take it?",
    reason: str = "You said before 3pm.",
    replies: list[str] | None = None,
    field_requested: str | None = None,
    at_ms: int = 1794081610000,
    tool_call_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """An ask_user tool-calls message. Returns (tool_call_id, body)."""
    args: dict[str, Any] = {
        "question": question,
        "reason": reason,
        "suggested_replies": replies or ["Ask for Fri", "Accept 4pm", "Decline"],
        "confidence": "low",
        "dimensions": {
            "needs_ask_first_field": field_requested is not None,
            "involves_money": False,
            "meets_constraints": False,
            "matches_fallback": False,
            "answerable_from_brief": True,
            "understood": True,
        },
    }
    if field_requested is not None:
        args["field_requested"] = field_requested
    tc_id = tool_call_id or f"tc-{uuid.uuid4()}"
    return tc_id, tool_body(call, [(tc_id, "ask_user", args)], timestamp=at_ms)


async def ping_rows(db_url: str, call: FreshCall) -> list[dict[str, Any]]:
    """Every ping of the call, oldest question first."""
    found = await rows(
        db_url,
        "select ping_id, kind, field_key, question, reason, suggested_replies, state,"
        " expires_at, remaining_ms from public.pings where call_id = %s order by source_ts",
        (call.call_id,),
    )
    keys = (
        "ping_id kind field_key question reason suggested_replies state expires_at remaining_ms"
    ).split()
    return [dict(zip(keys, r, strict=True)) for r in found]


async def outbox_rows(db_url: str, call: FreshCall) -> list[dict[str, Any]]:
    """Every outbox row of the call, in id order, with seconds until ``not_before``."""
    found = await rows(
        db_url,
        "select lane, kind, payload, state, attempts,"
        " extract(epoch from (not_before - now()))::float from public.outbox"
        " where call_id = %s order by id",
        (call.call_id,),
    )
    keys = "lane kind payload state attempts due_in_s".split()
    return [dict(zip(keys, r, strict=True)) for r in found]


async def event_rows(db_url: str, call: FreshCall) -> list[tuple[str, dict[str, Any]]]:
    """(type, payload) of every call event, in seq order."""
    found = await rows(
        db_url,
        "select type, payload from public.call_events where call_id = %s order by seq",
        (call.call_id,),
    )
    return [(r[0], r[1]) for r in found]


async def call_status(db_url: str, call: FreshCall) -> str:
    found = await rows(db_url, "select status from public.calls where id = %s", (call.call_id,))
    return str(found[0][0])


async def make_due(db_url: str, call: FreshCall) -> None:
    """Let every scheduled control row be sent now."""
    async with await psycopg.AsyncConnection.connect(db_url, autocommit=True) as conn:
        await conn.execute(
            "update public.outbox set not_before = now() where call_id = %s", (call.call_id,)
        )


async def expire_now(db_url: str, call: FreshCall) -> None:
    """Run the open ping's clock out."""
    async with await psycopg.AsyncConnection.connect(db_url, autocommit=True) as conn:
        await conn.execute(
            "update public.pings set expires_at = clock_timestamp() - interval '1 second'"
            " where call_id = %s and state = 'open'",
            (call.call_id,),
        )


def utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)
