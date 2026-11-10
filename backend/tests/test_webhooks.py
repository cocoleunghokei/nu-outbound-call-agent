"""Tests for the Vapi webhook ingress, the handler registries, dispatch_pending and redaction.

Fixtures in tests/fixtures/vapi/ use fake data in the shapes of the Vapi docs and the probe
kit (spikes/vapi/probe/webhook.py). Placeholders such as ``__CALL_ID__`` are substituted per
test, so every test runs against its own fresh call.
"""

import asyncio
import io
import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool, PoolTimeout
from pydantic import SecretStr

from app.calls_lock import end_call_transition
from app.config import Settings
from app.main import create_app
from app.registry import Registries
from app.secrets_registry import (
    RedactingFormatter,
    get_secret_registry,
    install_redacting_formatter,
)
from app.voice.port import VoiceUncertain
from app.webhooks.bind import BoundCall
from app.webhooks.redact import redact
from app.webhooks.registry import ToolCallContext
from app.webhooks.vapi import dispatch_pending
from tests.conftest import FreshCall, SupabaseEnv, create_fresh_call

FIXTURES = Path(__file__).parent / "fixtures" / "vapi"
SECRET = "whsec-fake-0123456789abcdef"
PATH = "/webhooks/vapi"
OUTCOME_ARGS: dict[str, Any] = {"outcome": "booked", "business_confirmed": True}
DIMENSIONS = {
    "needs_ask_first_field": False,
    "involves_money": False,
    "meets_constraints": True,
    "matches_fallback": False,
    "answerable_from_brief": True,
    "understood": True,
}


# --- helpers ----------------------------------------------------------------------------


def _control_url(call_id: uuid.UUID) -> str:
    """A path-based capability URL: the credential is in the path, not in a query string."""
    return f"https://control.example.test/v1/calls/{call_id}/control/cap{uuid.uuid4().hex}"


def _listen_url(call_id: uuid.UUID) -> str:
    return f"wss://listen.example.test/v1/calls/{call_id}/listen/cap{uuid.uuid4().hex}"


def _substitute(node: Any, subs: dict[str, str]) -> Any:
    if isinstance(node, dict):
        return {k: _substitute(v, subs) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(v, subs) for v in node]
    if isinstance(node, str):
        for placeholder, value in subs.items():
            node = node.replace(placeholder, value)
        return node
    return node


def fixture(name: str, call: FreshCall | None, **extra: str) -> dict[str, Any]:
    """Load tests/fixtures/vapi/<name>.json with this call's ids and fresh capability URLs."""
    call_id = call.call_id if call else uuid.uuid4()
    subs = {
        "__CALL_ID__": str(call_id),
        "__VAPI_CALL_ID__": f"vapi-{call_id}",
        "__CONTROL_URL__": _control_url(call_id),
        "__LISTEN_URL__": _listen_url(call_id),
        "__TOOL_CALL_1__": f"tc1-{uuid.uuid4()}",
        "__TOOL_CALL_2__": f"tc2-{uuid.uuid4()}",
    }
    subs.update(extra)
    body: dict[str, Any] = _substitute(json.loads((FIXTURES / f"{name}.json").read_text()), subs)
    return body


def tool_body(
    call: FreshCall,
    entries: list[tuple[str, str, Any]],
    *,
    control_url: str | None = None,
    timestamp: int | None = 1794081610000,
) -> dict[str, Any]:
    """A tool-calls message with one toolCallList entry per (id, name, arguments)."""
    message: dict[str, Any] = {
        "type": "tool-calls",
        "toolCallList": [
            {
                "id": tc_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": args if isinstance(args, str) else json.dumps(args),
                },
            }
            for tc_id, name, args in entries
        ],
        "call": {"id": f"vapi-{call.call_id}", "metadata": {"call_id": str(call.call_id)}},
    }
    if timestamp is not None:
        message["timestamp"] = timestamp
    if control_url is not None:
        message["call"]["monitor"] = {
            "controlUrl": control_url,
            "listenUrl": _listen_url(call.call_id),
        }
    return {"message": message}


def transcript_body(
    call: FreshCall,
    text: str,
    *,
    role: str = "user",
    timestamp: int | None = None,
    transcript_type: str = "final",
    control_url: str | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "type": "transcript",
        "role": role,
        "transcriptType": transcript_type,
        "transcript": text,
        "call": {"id": f"vapi-{call.call_id}", "metadata": {"call_id": str(call.call_id)}},
    }
    if timestamp is not None:
        message["timestamp"] = timestamp
    if control_url is not None:
        message["call"]["monitor"] = {
            "controlUrl": control_url,
            "listenUrl": _listen_url(call.call_id),
        }
    return {"message": message}


def make_settings(db_url: str = "", **overrides: Any) -> Settings:
    return Settings(database_url=db_url, vapi_webhook_secret=SecretStr(SECRET), **overrides)


@asynccontextmanager
async def serve(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Run the app's lifespan (startup included) and yield an in-process client."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def post(client: httpx.AsyncClient, body: Any, secret: str | None = SECRET) -> httpx.Response:
    headers = {"X-Vapi-Secret": secret} if secret is not None else {}
    return await client.post(PATH, json=body, headers=headers)


def registries(app: FastAPI) -> Registries:
    reg: Registries = app.state.registries
    return reg


async def rows(db_url: str, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    async with await psycopg.AsyncConnection.connect(db_url, autocommit=True) as conn:
        return await (await conn.execute(sql, params)).fetchall()


async def raw_bodies(db_url: str, call: FreshCall) -> list[str]:
    found = await rows(
        db_url,
        "select body::text from public.vapi_raw_events where vapi_call_id = %s order by id",
        (f"vapi-{call.call_id}",),
    )
    return [r[0] for r in found]


async def failed_errors(db_url: str, call: FreshCall) -> list[str]:
    found = await rows(
        db_url,
        "select error from public.call_events_failed where call_id = %s order by created_at",
        (call.call_id,),
    )
    return [r[0] for r in found]


async def receipts(db_url: str, call: FreshCall) -> dict[str, tuple[Any, ...]]:
    found = await rows(
        db_url,
        "select tool_call_id, state, result, error, arguments::text from public.tool_calls"
        " where call_id = %s",
        (call.call_id,),
    )
    return {r[0]: r[1:] for r in found}


async def events(db_url: str, call: FreshCall) -> list[tuple[Any, ...]]:
    return await rows(
        db_url,
        "select seq, type, dedup_key, payload from public.call_events where call_id = %s"
        " order by seq",
        (call.call_id,),
    )


class Recorder:
    """A test tool handler that records each invocation and returns a fixed result."""

    def __init__(self, result: str = "ok-handled") -> None:
        self.result = result
        self.seen: list[ToolCallContext] = []

    async def __call__(self, conn: AsyncConnection[Any], ctx: ToolCallContext) -> str:
        self.seen.append(ctx)
        return self.result

    def ids(self) -> list[str]:
        return [c.tool_call_id for c in self.seen]


# --- auth and request shape (no database) -----------------------------------------------


class _TimeoutPool:
    """Stands in for a pool whose connections are all busy."""

    def connection(self) -> "_TimeoutPool":
        return self

    async def __aenter__(self) -> None:
        raise PoolTimeout("couldn't get a connection after 0.10 sec")

    async def __aexit__(self, *exc: object) -> None:
        return None


class _ExplodingPool:
    """Fails the test if the ingress touches the database."""

    def connection(self) -> None:
        raise AssertionError("the ingress touched the database")


def test_pool_timeout_returns_503() -> None:
    app = create_app(make_settings())
    with TestClient(app) as client:
        app.state.pool = _TimeoutPool()
        body = fixture("status-update", None)
        response = client.post(PATH, json=body, headers={"X-Vapi-Secret": SECRET})
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "db_unavailable",
            "message": "Database unavailable, retry later",
            "retryable": True,
        }
    }


def test_no_database_configured_returns_503() -> None:
    with TestClient(create_app(make_settings())) as client:
        response = client.post(
            PATH, json=fixture("status-update", None), headers={"X-Vapi-Secret": SECRET}
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "db_unavailable"
    assert response.json()["error"]["retryable"] is True


def test_empty_configured_secret_rejects_everything() -> None:
    app = create_app(Settings(vapi_webhook_secret=SecretStr("")))
    with TestClient(app) as client:
        app.state.pool = _ExplodingPool()
        response = client.post(PATH, json=fixture("status-update", None), headers={})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_ip_allowlist_rejects_outside_vapi_range() -> None:
    app = create_app(make_settings(vapi_ip_allowlist=True))
    with TestClient(app) as client:  # TestClient's peer is "testclient", not a Vapi address
        app.state.pool = _ExplodingPool()
        response = client.post(
            PATH, json=fixture("status-update", None), headers={"X-Vapi-Secret": SECRET}
        )
    assert response.status_code == 401


def test_partial_transcript_never_touches_the_database() -> None:
    app = create_app(make_settings())
    with TestClient(app) as client:
        app.state.pool = _ExplodingPool()
        response = client.post(
            PATH, json=fixture("transcript-partial", None), headers={"X-Vapi-Secret": SECRET}
        )
    assert (response.status_code, response.json()) == (200, {})


# --- redaction and logging (no database) ------------------------------------------------


def test_redact_nested_token_urls() -> None:
    control = "https://control.example.test/v1/calls/abc/control/capSECRETPATH"
    body = {
        "message": {
            "call": {
                "monitor": {"controlUrl": control, "listenUrl": "wss://listen.example.test/x"},
                "notes": [
                    {"link": "see https://cdn.example.test/file.wav?X-Amz-Signature=abc123 now"},
                    {"link": "https://api.example.test/hook?api_key=k-123&x=1"},
                    {"link": "https://api.example.test/page?x=1"},
                    {"deep": {"deeper": [f"posted to {control}"]}},
                ],
                "Authorization": "Bearer fake-token",
                "passwordHint": "hunter2-fake",
                "count": 3,
            },
            "escaped": "sent to " + control.replace("/", "\\/"),
        }
    }
    original = json.dumps(body)
    out = redact(body, {control})
    text = json.dumps(out)
    assert json.dumps(body) == original  # works on a deep copy
    assert "capSECRETPATH" not in text
    assert "listen.example.test" not in text  # key rule: *Url
    assert "abc123" not in text
    assert "k-123" not in text
    assert "fake-token" not in text
    assert "hunter2" not in text
    notes = out["message"]["call"]["notes"]
    assert notes[0]["link"] == "see [redacted] now"
    assert notes[2]["link"] == "https://api.example.test/page?x=1"
    assert notes[3]["deep"]["deeper"] == ["posted to [redacted]"]
    assert out["message"]["escaped"] == "sent to [redacted]"
    assert out["message"]["call"]["count"] == 3


def test_redact_decodes_embedded_json_and_marks_undecodable_arguments() -> None:
    control = "https://control.example.test/v1/calls/abc/control/capEMBEDDED"
    args = json.dumps({"question": f"ok {control}", "token": "t-1"}).replace("/", "\\/")
    body = {
        "toolCallList": [
            {"id": "a", "function": {"name": "ask_user", "arguments": args}},
            {"id": "b", "function": {"name": "ask_user", "arguments": "{oops"}},
        ],
        "transcript": "[inaudible] Confirmed for Friday",
    }
    out = redact(body, {control})
    first = json.loads(out["toolCallList"][0]["function"]["arguments"])
    assert first == {"question": "ok [redacted]", "token": "[redacted]"}
    assert out["toolCallList"][1]["function"]["arguments"] == "[redacted:undecodable]"
    assert out["toolCallList"][1]["id"] == "b"
    assert out["transcript"] == "[inaudible] Confirmed for Friday"


def test_formatter_redacts_traceback_from_adapter_outside_request() -> None:
    control = _control_url(uuid.uuid4())
    get_secret_registry().add(control)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter("%(levelname)s %(name)s %(message)s"))
    logger = logging.getLogger("app.voice.vapi.test_formatter")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        try:
            raise VoiceUncertain(f"voice platform timeout sending to {control}")
        except VoiceUncertain:
            logger.exception("control send failed for %s", control)
    finally:
        logger.removeHandler(handler)
    output = stream.getvalue()
    assert "Traceback" in output and "VoiceUncertain" in output
    assert control not in output
    assert control.rsplit("/", 1)[1] not in output
    assert "[redacted]" in output


def test_install_redacting_formatter_wraps_root_and_uvicorn_handlers() -> None:
    root_handler = logging.StreamHandler(io.StringIO())
    root_handler.setFormatter(logging.Formatter("%(message)s"))
    uvicorn_handler = logging.StreamHandler(io.StringIO())
    logging.getLogger().addHandler(root_handler)
    logging.getLogger("uvicorn.error").addHandler(uvicorn_handler)
    try:
        install_redacting_formatter()
        install_redacting_formatter()  # idempotent: no double wrapping
        assert isinstance(root_handler.formatter, RedactingFormatter)
        assert isinstance(uvicorn_handler.formatter, RedactingFormatter)
        assert not isinstance(root_handler.formatter.inner, RedactingFormatter)
        secret = f"https://control.example.test/x/cap{uuid.uuid4().hex}"
        get_secret_registry().add(secret)
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "bad %s", (secret,), None)
        assert root_handler.format(record) == "bad [redacted]"
    finally:
        logging.getLogger().removeHandler(root_handler)
        logging.getLogger("uvicorn.error").removeHandler(uvicorn_handler)


# --- database ---------------------------------------------------------------------------


@pytest.mark.db
def test_wrong_secret_401_writes_nothing(supabase_env: SupabaseEnv, fresh_call: FreshCall) -> None:
    async def scenario() -> None:
        app = create_app(make_settings(supabase_env.db_url))
        async with serve(app) as client:
            wrong = await post(client, fixture("status-update", fresh_call), secret="nope")
            missing = await post(client, fixture("status-update", fresh_call), secret=None)
        for response in (wrong, missing):
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "unauthorized"
            assert response.json()["error"]["retryable"] is False
        assert await raw_bodies(supabase_env.db_url, fresh_call) == []
        assert (
            await rows(
                supabase_env.db_url,
                "select 1 from public.call_secrets where call_id = %s",
                (fresh_call.call_id,),
            )
            == []
        )

    asyncio.run(scenario())


@pytest.mark.db
def test_unknown_call_raw_only_200(supabase_env: SupabaseEnv) -> None:
    async def scenario() -> None:
        ghost = FreshCall(call_id=uuid.uuid4(), user_id=uuid.uuid4(), brief_id=uuid.uuid4())
        app = create_app(make_settings(supabase_env.db_url))
        recorder = Recorder()
        registries(app).tools.register_tool_handler("ask_user", recorder)
        async with serve(app) as client:
            response = await post(client, fixture("tool-calls", ghost))
        assert (response.status_code, response.json()) == (200, {})
        found = await rows(
            supabase_env.db_url,
            "select call_id, message_type from public.vapi_raw_events where vapi_call_id = %s",
            (f"vapi-{ghost.call_id}",),
        )
        assert found == [(None, "tool-calls")]
        assert recorder.seen == []

    asyncio.run(scenario())


@pytest.mark.db
def test_mismatched_vapi_call_id_raw_only(supabase_env: SupabaseEnv, fresh_call: FreshCall) -> None:
    async def scenario() -> None:
        body = transcript_body(fresh_call, "Hello?", timestamp=1794081605000)
        body["message"]["call"]["id"] = "vapi-someone-else"
        app = create_app(make_settings(supabase_env.db_url))
        async with serve(app) as client:
            response = await post(client, body)
        assert response.status_code == 200
        found = await rows(
            supabase_env.db_url,
            "select call_id from public.vapi_raw_events where vapi_call_id = 'vapi-someone-else'"
            " and body::text like %s",
            (f"%{fresh_call.call_id}%",),
        )
        assert found == [(None,)]
        assert await events(supabase_env.db_url, fresh_call) == []

    asyncio.run(scenario())


@pytest.mark.db
def test_duplicate_tool_call_runs_handler_once_and_returns_same_result(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        tc = f"tc-{uuid.uuid4()}"
        body = tool_body(fresh_call, [(tc, "report_outcome", OUTCOME_ARGS)])
        app = create_app(make_settings(supabase_env.db_url))
        recorder = Recorder("Outcome recorded.")
        registries(app).tools.register_tool_handler("report_outcome", recorder)
        async with serve(app) as client:
            first = await post(client, body)
            second = await post(client, body)
        expected = {"results": [{"toolCallId": tc, "result": "Outcome recorded."}]}
        assert first.json() == expected
        assert second.json() == expected
        assert recorder.ids() == [tc]
        state = await receipts(supabase_env.db_url, fresh_call)
        assert state[tc][:2] == ("handled", "Outcome recorded.")
        assert len(await raw_bodies(supabase_env.db_url, fresh_call)) == 2

    asyncio.run(scenario())


@pytest.mark.db
def test_two_tool_calls_in_one_message_both_dispatched(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        body = fixture("tool-calls", fresh_call)
        ids = [entry["id"] for entry in body["message"]["toolCallList"]]
        app = create_app(make_settings(supabase_env.db_url))
        ask, decide = Recorder("Waiting for Alex Chen."), Recorder("Noted.")
        registries(app).tools.register_tool_handler("ask_user", ask)
        registries(app).tools.register_tool_handler("report_decision", decide)
        async with serve(app) as client:
            response = await post(client, body)
        assert response.json() == {
            "results": [
                {"toolCallId": ids[0], "result": "Waiting for Alex Chen."},
                {"toolCallId": ids[1], "result": "Noted."},
            ]
        }
        assert ask.ids() == [ids[0]] and decide.ids() == [ids[1]]
        ctx = ask.seen[0]
        assert ctx.call_id == fresh_call.call_id and ctx.user_id == fresh_call.user_id
        assert ctx.name == "ask_user"
        assert ctx.arguments["question"] == "They offered 4pm Thursday. Take it?"
        assert ctx.source_ts is not None and ctx.source_ts.year == 2026
        state = await receipts(supabase_env.db_url, fresh_call)
        assert {k: v[0] for k, v in state.items()} == {ids[0]: "handled", ids[1]: "handled"}

    asyncio.run(scenario())


@pytest.mark.db
def test_unregistered_tool_left_pending(supabase_env: SupabaseEnv, fresh_call: FreshCall) -> None:
    async def scenario() -> None:
        tc = f"tc-{uuid.uuid4()}"
        app = create_app(make_settings(supabase_env.db_url))
        async with serve(app) as client:
            response = await post(
                client, tool_body(fresh_call, [(tc, "report_outcome", OUTCOME_ARGS)])
            )
        assert response.json() == {"results": [{"toolCallId": tc, "result": "received"}]}
        state = await receipts(supabase_env.db_url, fresh_call)
        assert state[tc][:3] == ("pending", None, None)
        assert json.loads(state[tc][3]) == OUTCOME_ARGS

    asyncio.run(scenario())


@pytest.mark.db
def test_pending_then_registered_dispatches_exactly_once(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        tc = f"tc-{uuid.uuid4()}"
        body = tool_body(fresh_call, [(tc, "report_outcome", OUTCOME_ARGS)])
        app = create_app(make_settings(supabase_env.db_url))
        async with serve(app) as client:
            assert (await post(client, body)).json()["results"][0]["result"] == "received"
            recorder = Recorder("Outcome recorded.")
            registries(app).tools.register_tool_handler("report_outcome", recorder)
            pool: AsyncConnectionPool = app.state.pool
            first = await dispatch_pending(pool, tools=registries(app).tools)
            await dispatch_pending(pool, tools=registries(app).tools)
            redelivered = await post(client, body)
        assert first >= 1
        assert recorder.ids().count(tc) == 1
        assert redelivered.json() == {
            "results": [{"toolCallId": tc, "result": "Outcome recorded."}]
        }
        state = await receipts(supabase_env.db_url, fresh_call)
        assert state[tc][:2] == ("handled", "Outcome recorded.")

    asyncio.run(scenario())


@pytest.mark.db
def test_bad_second_tool_entry_keeps_first(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        good, bad = f"tc-good-{uuid.uuid4()}", f"tc-bad-{uuid.uuid4()}"
        body = tool_body(
            fresh_call,
            [(good, "report_outcome", OUTCOME_ARGS), (bad, "report_outcome", {"outcome": 7})],
        )
        app = create_app(make_settings(supabase_env.db_url))
        recorder = Recorder()
        registries(app).tools.register_tool_handler("report_outcome", recorder)
        async with serve(app) as client:
            response = await post(client, body)
        assert response.status_code == 200
        assert response.json()["results"][0] == {"toolCallId": good, "result": "ok-handled"}
        assert recorder.ids() == [good]
        state = await receipts(supabase_env.db_url, fresh_call)
        assert set(state) == {good}
        errors = await failed_errors(supabase_env.db_url, fresh_call)
        assert len(errors) == 1 and "report_outcome" in errors[0]

    asyncio.run(scenario())


@pytest.mark.db
def test_identical_utterances_without_timestamps_both_stored(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        body = transcript_body(fresh_call, "Yes.", timestamp=None)
        app = create_app(make_settings(supabase_env.db_url))
        async with serve(app) as client:
            await post(client, body)
            await post(client, body)
        found = await events(supabase_env.db_url, fresh_call)
        assert [(e[1], e[2], e[3]["text"]) for e in found] == [
            ("transcript.final", None, "Yes."),
            ("transcript.final", None, "Yes."),
        ]
        assert found[0][3]["source_start_ts"] is None

    asyncio.run(scenario())


@pytest.mark.db
def test_duplicate_final_transcript_one_event(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        body = fixture("transcript-final", fresh_call)
        app = create_app(make_settings(supabase_env.db_url))
        async with serve(app) as client:
            assert (await post(client, body)).json() == {}
            assert (await post(client, body)).json() == {}
        found = await events(supabase_env.db_url, fresh_call)
        assert len(found) == 1
        seq, type_, dedup_key, payload = found[0]
        assert type_ == "transcript.final"
        assert dedup_key == "transcript:business:2026-11-07T20:00:05.123Z"
        assert payload == {
            "role": "business",
            "text": "We have Thursday at 4.",
            "source_start_ts": "2026-11-07T20:00:05.123Z",
        }
        assert len(await raw_bodies(supabase_env.db_url, fresh_call)) == 2

    asyncio.run(scenario())


@pytest.mark.db
def test_dedup_stable_across_app_restart(supabase_env: SupabaseEnv, fresh_call: FreshCall) -> None:
    async def scenario() -> None:
        tools_body = fixture("tool-calls", fresh_call)
        transcript = fixture("transcript-final", fresh_call)
        counts: list[int] = []
        responses = []
        for _ in range(2):  # two app instances, each with its own pool and registries
            app = create_app(make_settings(supabase_env.db_url))
            ask, decide = Recorder("asked"), Recorder("decided")
            registries(app).tools.register_tool_handler("ask_user", ask)
            registries(app).tools.register_tool_handler("report_decision", decide)
            async with serve(app) as client:
                responses.append((await post(client, tools_body)).json())
                await post(client, transcript)
            counts.append(len(ask.seen) + len(decide.seen))
        assert counts == [2, 0]
        assert responses[0] == responses[1]
        assert len(await events(supabase_env.db_url, fresh_call)) == 1
        assert len(await receipts(supabase_env.db_url, fresh_call)) == 2
        assert len(await raw_bodies(supabase_env.db_url, fresh_call)) == 4

    asyncio.run(scenario())


@pytest.mark.db
def test_path_based_control_url_redacted_in_exception_and_args(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        control = _control_url(fresh_call.call_id)
        tc = f"tc-{uuid.uuid4()}"
        args = {**OUTCOME_ARGS, "open_items": [f"call back via {control}"]}
        body = tool_body(fresh_call, [(tc, "report_outcome", args)], control_url=control)

        async def leaky(conn: AsyncConnection[Any], ctx: ToolCallContext) -> str:
            raise VoiceUncertain(f"voice platform timeout sending to {control}")

        app = create_app(make_settings(supabase_env.db_url))
        registries(app).tools.register_tool_handler("report_outcome", leaky)
        async with serve(app) as client:
            response = await post(client, body)
        assert response.json() == {"results": [{"toolCallId": tc, "result": "received"}]}
        token = control.rsplit("/", 1)[1]
        state = await receipts(supabase_env.db_url, fresh_call)
        _, _, error, arguments = state[tc]
        errors = await failed_errors(supabase_env.db_url, fresh_call)
        stored = [error, arguments, *errors, *await raw_bodies(supabase_env.db_url, fresh_call)]
        assert len(errors) == 1 and "VoiceUncertain" in errors[0]
        for text in stored:
            assert token not in text
        assert json.loads(arguments)["open_items"] == ["call back via [redacted]"]

    asyncio.run(scenario())


@pytest.mark.db
def test_late_webhook_after_end_does_not_restore_secrets(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        app = create_app(make_settings(supabase_env.db_url))
        async with serve(app) as client:
            await post(client, fixture("status-update", fresh_call))
            assert (
                len(
                    await rows(
                        supabase_env.db_url,
                        "select 1 from public.call_secrets where call_id = %s",
                        (fresh_call.call_id,),
                    )
                )
                == 1
            )
            async with await psycopg.AsyncConnection.connect(supabase_env.db_url) as conn:
                async with conn.transaction():
                    assert await end_call_transition(
                        conn, fresh_call.call_id, reason="test", source="backend"
                    )
            late = fixture("status-update", fresh_call)
            late_control = late["message"]["call"]["monitor"]["controlUrl"]
            response = await post(client, late)
        assert response.status_code == 200
        assert (
            await rows(
                supabase_env.db_url,
                "select 1 from public.call_secrets where call_id = %s",
                (fresh_call.call_id,),
            )
            == []
        )
        for text in await raw_bodies(supabase_env.db_url, fresh_call):
            assert late_control not in text
        assert late_control in get_secret_registry().all()  # still redacted from logs

    asyncio.run(scenario())


@pytest.mark.db
def test_url_still_redacted_after_call_end(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> str:
        body = fixture("status-update", fresh_call)
        control = body["message"]["call"]["monitor"]["controlUrl"]
        app = create_app(make_settings(supabase_env.db_url))
        async with serve(app) as client:
            await post(client, body)
        async with await psycopg.AsyncConnection.connect(supabase_env.db_url) as conn:
            async with conn.transaction():
                await end_call_transition(conn, fresh_call.call_id, reason="t", source="backend")
            await conn.execute("delete from public.calls where id = %s", (fresh_call.call_id,))
            await conn.commit()
        return str(control)

    control = asyncio.run(scenario())
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter("%(message)s"))
    logger = logging.getLogger("app.outbox.test_delayed")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        try:
            raise VoiceUncertain(f"late send to {control} timed out")
        except VoiceUncertain:
            logger.exception("delayed control send to %s failed", control)
    finally:
        logger.removeHandler(handler)
    assert control not in stream.getvalue()
    assert "delayed control send to [redacted] failed" in stream.getvalue()


@pytest.mark.db
def test_dispatch_pending_failing_row_then_ok_row(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        first, second = f"a-{uuid.uuid4()}", f"b-{uuid.uuid4()}"
        body = tool_body(
            fresh_call,
            [(first, "report_outcome", OUTCOME_ARGS), (second, "report_outcome", OUTCOME_ARGS)],
        )
        intake = create_app(make_settings(supabase_env.db_url))
        async with serve(intake) as client:
            await post(client, body)

        seen: list[str] = []

        async def picky(conn: AsyncConnection[Any], ctx: ToolCallContext) -> str:
            seen.append(ctx.tool_call_id)
            if ctx.tool_call_id == first:
                raise RuntimeError("handler exploded on the first row")
            return "ok-handled"

        restarted = create_app(make_settings(supabase_env.db_url))
        registries(restarted).tools.register_tool_handler("report_outcome", picky)
        async with serve(restarted) as client:  # startup runs dispatch_pending
            health = await client.get("/health")
        assert health.status_code == 200
        assert seen.index(first) < seen.index(second)
        state = await receipts(supabase_env.db_url, fresh_call)
        assert state[first][0] == "failed" and "handler exploded" in state[first][2]
        assert state[second][:2] == ("handled", "ok-handled")
        errors = await failed_errors(supabase_env.db_url, fresh_call)
        assert len(errors) == 1 and "handler exploded" in errors[0]

    asyncio.run(scenario())


@pytest.mark.db
def test_handler_failure_keeps_receipt_and_redelivery_does_not_rerun(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        tc = f"tc-{uuid.uuid4()}"
        body = tool_body(fresh_call, [(tc, "report_outcome", OUTCOME_ARGS)])
        calls: list[str] = []

        async def broken(conn: AsyncConnection[Any], ctx: ToolCallContext) -> str:
            calls.append(ctx.tool_call_id)
            raise RuntimeError("downstream refused")

        app = create_app(make_settings(supabase_env.db_url))
        registries(app).tools.register_tool_handler("report_outcome", broken)
        async with serve(app) as client:
            first = await post(client, body)
            second = await post(client, body)
        expected = {"results": [{"toolCallId": tc, "result": "received"}]}
        assert first.status_code == 200 and first.json() == expected
        assert second.json() == expected
        assert calls == [tc]
        state = await receipts(supabase_env.db_url, fresh_call)
        assert state[tc][0] == "failed" and "downstream refused" in state[tc][2]
        assert len(await failed_errors(supabase_env.db_url, fresh_call)) == 1

    asyncio.run(scenario())


@pytest.mark.db
def test_partial_transcript_not_stored_anywhere(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        app = create_app(make_settings(supabase_env.db_url))
        async with serve(app) as client:
            response = await post(client, fixture("transcript-partial", fresh_call))
        assert (response.status_code, response.json()) == (200, {})
        assert await raw_bodies(supabase_env.db_url, fresh_call) == []
        assert await events(supabase_env.db_url, fresh_call) == []
        assert await failed_errors(supabase_env.db_url, fresh_call) == []
        assert (
            await rows(
                supabase_env.db_url,
                "select 1 from public.call_secrets where call_id = %s",
                (fresh_call.call_id,),
            )
            == []
        )

    asyncio.run(scenario())


@pytest.mark.db
def test_control_url_moved_to_call_secrets_and_redacted_everywhere(
    supabase_env: SupabaseEnv, fresh_call: FreshCall, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    async def scenario() -> tuple[str, str]:
        status = fixture("status-update", fresh_call)
        monitor = status["message"]["call"]["monitor"]
        control, listen = monitor["controlUrl"], monitor["listenUrl"]
        said = transcript_body(
            fresh_call, f"Use {control} please", timestamp=1794081606000, control_url=control
        )
        tc = f"tc-{uuid.uuid4()}"
        tools = tool_body(fresh_call, [(tc, "report_outcome", OUTCOME_ARGS)], control_url=control)
        for body in (said, tools):  # the same live monitor on every message
            body["message"]["call"]["monitor"] = dict(monitor)

        async def leaky(conn: AsyncConnection[Any], ctx: ToolCallContext) -> str:
            logging.getLogger("app.test.leaky").error("about to fail on %s", control)
            raise RuntimeError(f"could not reach {control} or {listen}")

        app = create_app(make_settings(supabase_env.db_url))
        registries(app).tools.register_tool_handler("report_outcome", leaky)
        async with serve(app) as client:
            for body in (status, said, tools):
                assert (await post(client, body)).status_code == 200
        stored = await rows(
            supabase_env.db_url,
            "select control_url, listen_url from public.call_secrets where call_id = %s",
            (fresh_call.call_id,),
        )
        assert stored == [(control, listen)]
        return str(control), str(listen)

    control, listen = asyncio.run(scenario())

    async def collect() -> list[str]:
        db = supabase_env.db_url
        texts = await raw_bodies(db, fresh_call)
        texts += [json.dumps(e[3]) for e in await events(db, fresh_call)]
        texts += await failed_errors(db, fresh_call)
        texts += [" ".join(str(v) for v in r) for r in (await receipts(db, fresh_call)).values()]
        return texts

    texts = asyncio.run(collect())
    assert len(texts) >= 6
    # caplog.text is formatted (so redacted); raw messages from our own code must be clean
    # too. The test's leaky handler logs the URL on purpose to exercise the formatter.
    own = [r.getMessage() for r in caplog.records if not r.name.startswith("app.test.")]
    assert "about to fail on [redacted]" in caplog.text
    caplog_text = caplog.text + " ".join(own)
    for text in [*texts, caplog_text]:
        assert control not in text and listen not in text
        assert control.rsplit("/", 1)[1] not in text
    said_events = [e for e in texts if "Use [redacted] please" in e]
    assert said_events


@pytest.mark.db
def test_json_string_tool_arguments_redacted_in_raw_row(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        control = _control_url(fresh_call.call_id)
        tc = f"tc-{uuid.uuid4()}"
        args = {**OUTCOME_ARGS, "password": "hunter2-fake", "open_items": [f"see {control}"]}
        escaped = json.dumps(args).replace("/", "\\/")
        assert "\\/" in escaped
        body = tool_body(fresh_call, [(tc, "report_outcome", escaped)], control_url=control)

        async def echo_fail(conn: AsyncConnection[Any], ctx: ToolCallContext) -> str:
            raise RuntimeError(f"rejected arguments {ctx.arguments!r}")

        app = create_app(make_settings(supabase_env.db_url))
        registries(app).tools.register_tool_handler("report_outcome", echo_fail)
        async with serve(app) as client:
            await post(client, body)
        token = control.rsplit("/", 1)[1]
        state = await receipts(supabase_env.db_url, fresh_call)
        errors = await failed_errors(supabase_env.db_url, fresh_call)
        assert len(errors) == 1 and "rejected arguments" in errors[0]
        for text in [*await raw_bodies(supabase_env.db_url, fresh_call), *errors, state[tc][3]]:
            assert "hunter2" not in text
            assert token not in text
        assert json.loads(state[tc][3])["password"] == "[redacted]"

    asyncio.run(scenario())


@pytest.mark.db
def test_undecodable_arguments_replaced_sibling_kept(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        broken, fine = f"tc-broken-{uuid.uuid4()}", f"tc-fine-{uuid.uuid4()}"
        body = tool_body(
            fresh_call,
            [(broken, "report_outcome", "{oops"), (fine, "report_outcome", OUTCOME_ARGS)],
        )
        app = create_app(make_settings(supabase_env.db_url))
        recorder = Recorder()
        registries(app).tools.register_tool_handler("report_outcome", recorder)
        async with serve(app) as client:
            response = await post(client, body)
        assert response.json()["results"][-1] == {"toolCallId": fine, "result": "ok-handled"}
        assert recorder.ids() == [fine]
        [raw] = await raw_bodies(supabase_env.db_url, fresh_call)
        entries = json.loads(raw)["message"]["toolCallList"]
        assert entries[0]["function"]["arguments"] == "[redacted:undecodable]"
        assert json.loads(entries[1]["function"]["arguments"]) == OUTCOME_ARGS
        assert set(await receipts(supabase_env.db_url, fresh_call)) == {fine}
        assert len(await failed_errors(supabase_env.db_url, fresh_call)) == 1

    asyncio.run(scenario())


@pytest.mark.db
def test_bracket_prefixed_transcript_text_preserved_and_secret_scrubbed(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        control = _control_url(fresh_call.call_id)
        body = transcript_body(
            fresh_call,
            f"[inaudible] Confirmed for Friday {control}",
            timestamp=1794081607000,
            control_url=control,
        )
        app = create_app(make_settings(supabase_env.db_url))
        async with serve(app) as client:
            await post(client, body)
        [event] = await events(supabase_env.db_url, fresh_call)
        assert event[3]["text"] == "[inaudible] Confirmed for Friday [redacted]"
        [raw] = await raw_bodies(supabase_env.db_url, fresh_call)
        assert json.loads(raw)["message"]["transcript"] == event[3]["text"]

    asyncio.run(scenario())


@pytest.mark.db
def test_handler_sql_error_records_failed_and_acks(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        bad, after = f"a-{uuid.uuid4()}", f"b-{uuid.uuid4()}"
        args = {
            "situation": "Offered 7pm",
            "action": "accept",
            "confidence": "high",
            "reason": "Fits",
            "dimensions": DIMENSIONS,
        }
        body = tool_body(
            fresh_call, [(bad, "report_outcome", OUTCOME_ARGS), (after, "report_decision", args)]
        )

        async def bad_sql(conn: AsyncConnection[Any], ctx: ToolCallContext) -> str:
            await conn.execute("select * from public.no_such_table")
            return "unreachable"

        app = create_app(make_settings(supabase_env.db_url))
        registries(app).tools.register_tool_handler("report_outcome", bad_sql)
        registries(app).tools.register_tool_handler("report_decision", Recorder("Noted."))
        async with serve(app) as client:
            response = await post(client, body)
        assert response.status_code == 200
        assert response.json() == {
            "results": [
                {"toolCallId": bad, "result": "received"},
                {"toolCallId": after, "result": "Noted."},
            ]
        }
        state = await receipts(supabase_env.db_url, fresh_call)
        assert state[bad][0] == "failed" and "UndefinedTable" in state[bad][2]
        assert state[after][:2] == ("handled", "Noted.")
        errors = await failed_errors(supabase_env.db_url, fresh_call)
        assert len(errors) == 1 and "no_such_table" in errors[0]

    asyncio.run(scenario())


@pytest.mark.db
def test_handler_sql_failure_writes_failed_receipt_and_dead_letter(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    """The dispatch_pending path: a pending row whose late-registered handler runs bad SQL."""

    async def scenario() -> None:
        tc = f"tc-{uuid.uuid4()}"
        app = create_app(make_settings(supabase_env.db_url))

        async def bad_sql(conn: AsyncConnection[Any], ctx: ToolCallContext) -> str:
            if ctx.call_id == fresh_call.call_id:
                await conn.execute("insert into public.no_such_table values (1)")
            return "ok-handled"

        async with serve(app) as client:
            await post(client, tool_body(fresh_call, [(tc, "report_outcome", OUTCOME_ARGS)]))
            registries(app).tools.register_tool_handler("report_outcome", bad_sql)
            await dispatch_pending(app.state.pool, tools=registries(app).tools)
            redelivered = await post(
                client, tool_body(fresh_call, [(tc, "report_outcome", OUTCOME_ARGS)])
            )
        assert redelivered.json() == {"results": [{"toolCallId": tc, "result": "received"}]}
        state = await receipts(supabase_env.db_url, fresh_call)
        assert state[tc][0] == "failed" and "UndefinedTable" in state[tc][2]
        errors = await failed_errors(supabase_env.db_url, fresh_call)
        assert len(errors) == 1 and "no_such_table" in errors[0]
        raw_ids = await rows(
            supabase_env.db_url,
            "select raw_event_id from public.call_events_failed where call_id = %s",
            (fresh_call.call_id,),
        )
        assert raw_ids == [(f"tool_calls:{tc}",)]

    asyncio.run(scenario())


@pytest.mark.db
def test_raw_insert_failure_returns_503(supabase_env: SupabaseEnv, fresh_call: FreshCall) -> None:
    """Postgres jsonb rejects \\u0000, so the raw insert fails for real; nothing commits."""

    async def scenario() -> None:
        body = fixture("status-update", fresh_call)
        body["message"]["note"] = "nul\u0000byte"
        app = create_app(make_settings(supabase_env.db_url))
        async with serve(app) as client:
            response = await post(client, body)
        assert response.status_code == 503
        assert response.json()["error"] == {
            "code": "db_unavailable",
            "message": "Database unavailable, retry later",
            "retryable": True,
        }
        assert await raw_bodies(supabase_env.db_url, fresh_call) == []
        assert (
            await rows(
                supabase_env.db_url,
                "select 1 from public.call_secrets where call_id = %s",
                (fresh_call.call_id,),
            )
            == []
        )

    asyncio.run(scenario())


@pytest.mark.db
def test_commit_failure_returns_503_not_200(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    """A deferred constraint makes COMMIT itself fail after every unit succeeded."""

    async def scenario() -> None:
        async def defer_violation(
            conn: AsyncConnection[Any], call: BoundCall, body: dict[str, Any]
        ) -> None:
            await conn.execute(
                "create temp table defer_check (x int unique deferrable initially deferred)"
                " on commit drop"
            )
            await conn.execute("insert into defer_check values (1), (1)")

        app = create_app(make_settings(supabase_env.db_url))
        registries(app).events.register_event_handler("brand-new-vapi-event", defer_violation)
        async with serve(app) as client:
            response = await post(client, fixture("unknown-type", fresh_call))
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "db_unavailable"
        assert await raw_bodies(supabase_env.db_url, fresh_call) == []

    asyncio.run(scenario())


@pytest.mark.db
def test_concurrent_dispatchers_and_webhook_no_deadlock(supabase_env: SupabaseEnv) -> None:
    async def scenario() -> None:
        db = supabase_env.db_url
        calls = [create_fresh_call(db) for _ in range(3)]
        pending_ids = {c.call_id: [f"p{i}-{uuid.uuid4()}" for i in range(4)] for c in calls}
        live_ids = {c.call_id: [f"l{i}-{uuid.uuid4()}" for i in range(2)] for c in calls}
        app = create_app(make_settings(db))
        handled: list[str] = []

        async def slow(conn: AsyncConnection[Any], ctx: ToolCallContext) -> str:
            # Touch the call row and the event log, as real owners will.
            await conn.execute("select pg_sleep(0.01)")
            await conn.execute(
                "update public.calls set latest_ask_at = now() where id = %s", (ctx.call_id,)
            )
            handled.append(ctx.tool_call_id)
            return "ok-handled"

        async with serve(app) as client:
            for call in calls:
                entries = [(tc, "report_outcome", OUTCOME_ARGS) for tc in pending_ids[call.call_id]]
                await post(client, tool_body(call, entries))
            registries(app).tools.register_tool_handler("report_outcome", slow)
            pool: AsyncConnectionPool = app.state.pool

            async def dispatcher() -> int:
                total = 0
                while (n := await dispatch_pending(pool, tools=registries(app).tools)) > 0:
                    total += n
                return total

            async def intake(call: FreshCall) -> httpx.Response:
                entries = [(tc, "report_outcome", OUTCOME_ARGS) for tc in live_ids[call.call_id]]
                transcript = transcript_body(call, "Still there?", timestamp=1794081608000)
                await post(client, transcript)
                return await post(client, tool_body(call, entries))

            results = await asyncio.wait_for(
                asyncio.gather(dispatcher(), dispatcher(), *(intake(c) for c in calls)),
                timeout=60,
            )
        dispatched = results[0] + results[1]
        ours = [tc for ids in (*pending_ids.values(), *live_ids.values()) for tc in ids]
        assert dispatched >= 12
        for tc in ours:
            assert handled.count(tc) == 1, tc
        for response in results[2:]:
            assert response.status_code == 200
            assert {r["result"] for r in response.json()["results"]} == {"ok-handled"}
        for call in calls:
            state = await receipts(db, call)
            assert {v[0] for v in state.values()} == {"handled"}
            assert len(state) == 6
            assert await failed_errors(db, call) == []

    asyncio.run(scenario())


@pytest.mark.db
def test_scores_go_to_agent_scores_not_events(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        body = fixture("tool-calls", fresh_call)
        ids = [entry["id"] for entry in body["message"]["toolCallList"]]

        async def ask(conn: AsyncConnection[Any], ctx: ToolCallContext) -> str:
            # A real owner builds its payload from an allow-list of the arguments.
            await conn.execute("select 1")
            return "asked"

        app = create_app(make_settings(supabase_env.db_url))
        decide = Recorder("decided")
        registries(app).tools.register_tool_handler("ask_user", ask)
        registries(app).tools.register_tool_handler("report_decision", decide)
        async with serve(app) as client:
            await post(client, body)
            await post(client, fixture("transcript-final", fresh_call))
        scores = await rows(
            supabase_env.db_url,
            "select tool_call_id, tool, confidence, dimensions from public.agent_scores"
            " where call_id = %s order by tool",
            (fresh_call.call_id,),
        )
        assert [(s[0], s[1], s[2]) for s in scores] == [
            (ids[0], "ask_user", "low"),
            (ids[1], "report_decision", "high"),
        ]
        assert scores[1][3]["meets_constraints"] is True
        assert "confidence" not in decide.seen[0].arguments
        assert "dimensions" not in decide.seen[0].arguments
        for event in await events(supabase_env.db_url, fresh_call):
            assert "confidence" not in json.dumps(event[3])
            assert "dimensions" not in json.dumps(event[3])

    asyncio.run(scenario())


@pytest.mark.db
def test_unknown_type_acked_raw_only(supabase_env: SupabaseEnv, fresh_call: FreshCall) -> None:
    async def scenario() -> None:
        app = create_app(make_settings(supabase_env.db_url))
        async with serve(app) as client:
            response = await post(client, fixture("unknown-type", fresh_call))
        assert (response.status_code, response.json()) == (200, {})
        found = await rows(
            supabase_env.db_url,
            "select call_id, message_type from public.vapi_raw_events where vapi_call_id = %s",
            (f"vapi-{fresh_call.call_id}",),
        )
        assert found == [(fresh_call.call_id, "brand-new-vapi-event")]
        assert await events(supabase_env.db_url, fresh_call) == []
        assert await failed_errors(supabase_env.db_url, fresh_call) == []

    asyncio.run(scenario())


@pytest.mark.db
def test_event_handler_receives_bound_call_and_redacted_body(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        body = fixture("end-of-call-report", fresh_call)
        control = body["message"]["call"]["monitor"]["controlUrl"]
        seen: list[tuple[BoundCall, dict[str, Any]]] = []

        async def on_report(
            conn: AsyncConnection[Any], call: BoundCall, raw: dict[str, Any]
        ) -> None:
            seen.append((call, raw))

        app = create_app(make_settings(supabase_env.db_url))
        registries(app).events.register_event_handler("end-of-call-report", on_report)
        async with serve(app) as client:
            response = await post(client, body)
        assert (response.status_code, response.json()) == (200, {})
        [(call, raw)] = seen
        assert call.call_id == fresh_call.call_id and call.user_id == fresh_call.user_id
        assert control not in json.dumps(raw)
        assert raw["message"]["artifact"]["recordingUrl"] == "[redacted]"
        assert raw["message"]["summary"].startswith("Booked a table")

    asyncio.run(scenario())


@pytest.mark.db
def test_transcripts_display_order_uses_source_start_ts(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        later = transcript_body(fresh_call, "Thursday works.", timestamp=1794081609000)
        earlier = transcript_body(
            fresh_call, "Is Thursday okay?", role="assistant", timestamp=1794081608000
        )
        app = create_app(make_settings(supabase_env.db_url))
        async with serve(app) as client:
            await post(client, later)  # arrives first
            await post(client, earlier)
        by_seq = [e[3]["text"] for e in await events(supabase_env.db_url, fresh_call)]
        assert by_seq == ["Thursday works.", "Is Thursday okay?"]
        display = await rows(
            supabase_env.db_url,
            "select payload->>'role', payload->>'text', payload->>'source_start_ts'"
            " from public.call_events where call_id = %s and type = 'transcript.final'"
            " order by coalesce((payload->>'source_start_ts')::timestamptz, occurred_at), seq",
            (fresh_call.call_id,),
        )
        assert display == [
            ("agent", "Is Thursday okay?", "2026-11-07T20:00:08.000Z"),
            ("business", "Thursday works.", "2026-11-07T20:00:09.000Z"),
        ]

    asyncio.run(scenario())


# --- fix round 1: scores leave the receipt; BoundCall comes from the locked row ---------


async def score_rows(db_url: str, call: FreshCall) -> dict[str, int]:
    found = await rows(
        db_url,
        "select tool_call_id, count(*) from public.agent_scores where call_id = %s"
        " group by tool_call_id",
        (call.call_id,),
    )
    return {r[0]: r[1] for r in found}


def _assert_no_scores_on_receipts(state: dict[str, tuple[Any, ...]]) -> None:
    for tool_call_id, (receipt_state, _, _, arguments) in state.items():
        assert receipt_state in ("handled", "failed"), tool_call_id
        decoded = json.loads(arguments)
        assert "confidence" not in decoded and "dimensions" not in decoded, tool_call_id


@pytest.mark.db
def test_handled_scored_receipts_drop_scores_and_score_once(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        body = fixture("tool-calls", fresh_call)
        ids = [entry["id"] for entry in body["message"]["toolCallList"]]
        app = create_app(make_settings(supabase_env.db_url))
        ask, decide = Recorder("asked"), Recorder("decided")
        registries(app).tools.register_tool_handler("ask_user", ask)
        registries(app).tools.register_tool_handler("report_decision", decide)
        async with serve(app) as client:
            first = await post(client, body)
            second = await post(client, body)  # redelivery of already-handled scored calls
        assert first.json() == second.json()
        assert len(ask.seen) + len(decide.seen) == 2
        state = await receipts(supabase_env.db_url, fresh_call)
        assert set(state) == set(ids)
        _assert_no_scores_on_receipts(state)
        assert await score_rows(supabase_env.db_url, fresh_call) == {ids[0]: 1, ids[1]: 1}

    asyncio.run(scenario())


@pytest.mark.db
def test_failed_scored_receipt_drops_scores(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        body = fixture("tool-calls", fresh_call)

        async def broken(conn: AsyncConnection[Any], ctx: ToolCallContext) -> str:
            raise RuntimeError("owner failed")

        app = create_app(make_settings(supabase_env.db_url))
        registries(app).tools.register_tool_handler("ask_user", broken)
        registries(app).tools.register_tool_handler("report_decision", broken)
        async with serve(app) as client:
            await post(client, body)
        state = await receipts(supabase_env.db_url, fresh_call)
        assert {v[0] for v in state.values()} == {"failed"}
        _assert_no_scores_on_receipts(state)
        assert await score_rows(supabase_env.db_url, fresh_call) == {}

    asyncio.run(scenario())


@pytest.mark.db
def test_dispatch_pending_scored_receipt_drops_scores_and_scores_once(
    supabase_env: SupabaseEnv, fresh_call: FreshCall, tmp_path: Path
) -> None:
    async def scenario() -> None:
        body = fixture("tool-calls", fresh_call)
        ids = [entry["id"] for entry in body["message"]["toolCallList"]]
        # No owner packages, so no tool has a handler until the test registers one.
        app = create_app(make_settings(supabase_env.db_url), packages_root=tmp_path)
        async with serve(app) as client:
            await post(client, body)  # no handlers yet: both receipts pending, scores kept
            pending = await receipts(supabase_env.db_url, fresh_call)
            assert {v[0] for v in pending.values()} == {"pending"}
            assert all("confidence" in json.loads(v[3]) for v in pending.values())
            assert await score_rows(supabase_env.db_url, fresh_call) == {}
            registries(app).tools.register_tool_handler("ask_user", Recorder("asked"))
            registries(app).tools.register_tool_handler("report_decision", Recorder("decided"))
            await dispatch_pending(app.state.pool, tools=registries(app).tools)
            await dispatch_pending(app.state.pool, tools=registries(app).tools)
            await post(client, body)  # redelivery after dispatch
        state = await receipts(supabase_env.db_url, fresh_call)
        assert {v[0] for v in state.values()} == {"handled"}
        _assert_no_scores_on_receipts(state)
        assert await score_rows(supabase_env.db_url, fresh_call) == {ids[0]: 1, ids[1]: 1}

    asyncio.run(scenario())


@pytest.mark.db
def test_event_handler_sees_ended_at_from_locked_row(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    """The call ends while the webhook waits for the call lock: the handler must see it."""

    async def scenario() -> None:
        seen: list[BoundCall] = []

        async def on_status(
            conn: AsyncConnection[Any], call: BoundCall, raw: dict[str, Any]
        ) -> None:
            seen.append(call)

        app = create_app(make_settings(supabase_env.db_url))
        registries(app).events.register_event_handler("status-update", on_status)
        async with serve(app) as client:
            async with await psycopg.AsyncConnection.connect(supabase_env.db_url) as ender:
                async with ender.transaction():
                    assert await end_call_transition(
                        ender, fresh_call.call_id, reason="test", source="backend"
                    )
                    # bind_call's plain read still sees ended_at null; lock_call blocks here.
                    request = asyncio.create_task(
                        post(client, fixture("status-update", fresh_call))
                    )
                    await asyncio.sleep(0.5)
                    assert not request.done()
            response = await request
        assert response.status_code == 200
        [call] = seen
        assert call.ended_at is not None
        assert call.call_id == fresh_call.call_id and call.user_id == fresh_call.user_id

    asyncio.run(scenario())


async def _lock_not_available(conn: AsyncConnection[Any], ctx: ToolCallContext) -> str:
    """A tool handler that hits a transient database error (lock_timeout expired)."""
    raise psycopg.errors.LockNotAvailable("canceling statement due to lock timeout")


@pytest.mark.db
def test_transient_db_error_in_tool_handler_is_503_and_leaves_no_receipt(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    """A transient OperationalError must not mark the receipt failed: Vapi retries instead."""

    async def scenario() -> None:
        tc = f"tc-{uuid.uuid4()}"
        app = create_app(make_settings(supabase_env.db_url))
        registries(app).tools.register_tool_handler("report_outcome", _lock_not_available)
        async with serve(app) as client:
            response = await post(
                client, tool_body(fresh_call, [(tc, "report_outcome", OUTCOME_ARGS)])
            )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "db_unavailable"
        assert response.json()["error"]["retryable"] is True
        assert await receipts(supabase_env.db_url, fresh_call) == {}
        assert await failed_errors(supabase_env.db_url, fresh_call) == []
        assert await raw_bodies(supabase_env.db_url, fresh_call) == []

    asyncio.run(scenario())


@pytest.mark.db
def test_transient_db_error_in_dispatch_pending_keeps_row_pending(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    async def scenario() -> None:
        tc = f"tc-{uuid.uuid4()}"
        app = create_app(make_settings(supabase_env.db_url))
        async with serve(app) as client:
            await post(client, tool_body(fresh_call, [(tc, "report_outcome", OUTCOME_ARGS)]))
            registries(app).tools.register_tool_handler("report_outcome", _lock_not_available)
            await dispatch_pending(app.state.pool, tools=registries(app).tools)
            state = await receipts(supabase_env.db_url, fresh_call)
            assert state[tc][:3] == ("pending", None, None)
            assert await failed_errors(supabase_env.db_url, fresh_call) == []
            # The next tick, with the database healthy again, handles it (and leaves no
            # pending row behind for later tests' startup dispatch).
            recorder = Recorder("Outcome recorded.")
            registries(app).tools.register_tool_handler("report_outcome", recorder)
            await dispatch_pending(app.state.pool, tools=registries(app).tools)
        assert tc in recorder.ids()
        state = await receipts(supabase_env.db_url, fresh_call)
        assert state[tc][:2] == ("handled", "Outcome recorded.")

    asyncio.run(scenario())


@pytest.mark.db
def test_vapi_call_id_changed_while_waiting_for_lock_is_raw_only(
    supabase_env: SupabaseEnv, fresh_call: FreshCall
) -> None:
    """bind_call's unlocked read passes, but the locked row has another vapi_call_id."""

    async def scenario() -> None:
        tc = f"tc-{uuid.uuid4()}"
        async with await psycopg.AsyncConnection.connect(
            supabase_env.db_url, autocommit=True
        ) as setup:
            await setup.execute(
                "update public.calls set vapi_call_id = null where id = %s", (fresh_call.call_id,)
            )
        app = create_app(make_settings(supabase_env.db_url))
        recorder = Recorder()
        async with serve(app) as client:
            # After startup, so startup dispatch of other tests' pending rows can't reach it.
            registries(app).tools.register_tool_handler("report_outcome", recorder)
            async with await psycopg.AsyncConnection.connect(supabase_env.db_url) as rebinder:
                async with rebinder.transaction():
                    await rebinder.execute(
                        "update public.calls set vapi_call_id = %s where id = %s",
                        (f"vapi-other-{uuid.uuid4()}", fresh_call.call_id),
                    )
                    request = asyncio.create_task(
                        post(client, tool_body(fresh_call, [(tc, "report_outcome", OUTCOME_ARGS)]))
                    )
                    await asyncio.sleep(0.5)
                    assert not request.done()  # bind_call passed; lock_call is waiting
            response = await request
        assert (response.status_code, response.json()) == (200, {})
        assert [c for c in recorder.seen if c.call_id == fresh_call.call_id] == []
        assert await receipts(supabase_env.db_url, fresh_call) == {}
        found = await rows(
            supabase_env.db_url,
            "select call_id from public.vapi_raw_events where vapi_call_id = %s",
            (f"vapi-{fresh_call.call_id}",),
        )
        assert found == [(None,)]

    asyncio.run(scenario())
