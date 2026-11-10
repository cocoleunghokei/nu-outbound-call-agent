"""Tests for the local dev helpers (seed and replay). No database or network needed."""

import json
import uuid
from pathlib import Path

import httpx
import pytest

from app.dev.replay import replay_fixture
from app.dev.seed import NotLocalDatabase, assert_local_db

FIXTURE = Path(__file__).parent / "fixtures" / "vapi" / "happy_booking.jsonl"


def test_seed_refuses_non_local_db() -> None:
    for url in (
        "postgresql://postgres:pw@db.abcdefgh.supabase.co:5432/postgres",
        "postgresql://postgres:pw@10.0.0.5:5432/postgres",
        "postgresql://postgres:pw@localhost.evil.example:5432/postgres",
        "",
    ):
        with pytest.raises(NotLocalDatabase):
            assert_local_db(url)
    assert_local_db("postgresql://postgres:postgres@127.0.0.1:5432/postgres")
    assert_local_db("postgresql://postgres:postgres@localhost:54322/postgres")


def test_replay_rewrites_call_id_and_posts_each_line() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    call_id = uuid.uuid4()
    lines = [line for line in FIXTURE.read_text().splitlines() if line.strip()]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        statuses = replay_fixture(
            FIXTURE, call_id, "http://localhost:8000/webhooks/vapi", "local-secret", client
        )

    assert statuses == [200] * len(lines)
    assert len(seen) == len(lines) == 9
    types = []
    for request in seen:
        assert str(request.url) == "http://localhost:8000/webhooks/vapi"
        assert request.headers["X-Vapi-Secret"] == "local-secret"
        body = json.loads(request.content)
        assert body["message"]["call"]["metadata"]["call_id"] == str(call_id)
        types.append(body["message"]["type"])
    assert types == (["status-update"] + ["transcript"] * 6 + ["tool-calls", "end-of-call-report"])


def test_replay_stops_on_non_2xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="401"):
            replay_fixture(FIXTURE, uuid.uuid4(), "http://localhost:8000/x", "s", client)
