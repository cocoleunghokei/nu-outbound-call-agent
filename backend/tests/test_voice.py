"""Voice port, Vapi adapter (MockTransport), fake adapter and the prompt-safety guard."""

import asyncio
import copy
import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.errors import ApiError
from app.voice.fake import FakeVoicePlatform
from app.voice.port import (
    CreatedCall,
    UnsafeAssistantConfig,
    VoiceNotSent,
    VoiceRejected,
    VoiceUncertain,
)
from app.voice.prompt_safety import (
    AskFirstInPrompt,
    MissingVar,
    Snapshot,
    assert_prompt_safe,
    build_first_message,
    resolve_task_vars,
)
from app.voice.vapi import VapiVoice

CONTROL_URL = "https://control.vapi.test/secret-capability-token"
LISTEN_URL = "https://listen.vapi.test/secret-listen-token"

SNAPSHOT = Snapshot.from_rows(
    [
        ("calling_name", "Alex", "share_freely"),
        ("full_name", "Alex Chen", "ask_first"),
        ("dob", "2004-03-15", "ask_first"),
        ("apartment", "7B", "ask_first"),
        ("address", "12 Main St", "ask_first"),
        ("notes", "lives at 12 Main St near the park", "share_freely"),
        ("party_size", "4", "share_freely"),
    ]
)


def good_assistant(**overrides: Any) -> dict[str, Any]:
    assistant: dict[str, Any] = {
        "firstMessage": "Hi, I'm an AI assistant calling on behalf of Alex.",
        "model": {
            "provider": "openai",
            "model": "gpt-4o",
            "messages": [{"role": "system", "content": "Book a table for four."}],
            "tools": [{"type": "function", "function": {"name": "evil"}}],
        },
        "artifactPlan": {"recordingEnabled": False, "transcriptPlan": {"enabled": True}},
    }
    assistant.update(overrides)
    return assistant


class Recorder:
    """MockTransport handler that records requests and answers with a scripted response."""

    def __init__(self, respond: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond or self._ok

    @staticmethod
    def _ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={"id": "vapi-1", "monitor": {"controlUrl": CONTROL_URL, "listenUrl": LISTEN_URL}},
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._respond(request)


def vapi(recorder: Recorder) -> VapiVoice:
    return VapiVoice(
        "test-key", "pn-1", "https://api.vapi.test", transport=httpx.MockTransport(recorder)
    )


def create(adapter: Any, assistant: dict[str, Any] | None = None) -> CreatedCall:
    return asyncio.run(
        adapter.create_call(
            assistant or good_assistant(), "+15550100", {"call_id": "abc"}, SNAPSHOT
        )
    )


def raising(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def respond(request: httpx.Request) -> httpx.Response:
        raise exc

    return respond


@pytest.mark.parametrize(
    "plan",
    [
        {"recordingEnabled": True, "transcriptPlan": {"enabled": True}},
        {"transcriptPlan": {"enabled": True}},
    ],
)
def test_create_call_refuses_recording_enabled(plan: dict[str, Any]) -> None:
    recorder = Recorder()
    with pytest.raises(UnsafeAssistantConfig) as info:
        create(vapi(recorder), good_assistant(artifactPlan=plan))
    assert isinstance(info.value, ApiError)
    assert (info.value.code, info.value.status, info.value.retryable) == (
        "unsafe_assistant_config",
        500,
        False,
    )
    assert recorder.requests == []


@pytest.mark.parametrize(
    "plan",
    [
        {"recordingEnabled": False},
        {"recordingEnabled": False, "transcriptPlan": {"enabled": False}},
    ],
)
def test_create_call_refuses_missing_transcript_plan(plan: dict[str, Any]) -> None:
    recorder = Recorder()
    with pytest.raises(UnsafeAssistantConfig):
        create(vapi(recorder), good_assistant(artifactPlan=plan))
    assert recorder.requests == []


def test_create_call_sends_metadata_call_id() -> None:
    recorder = Recorder()
    created = create(vapi(recorder))
    assert created == CreatedCall("vapi-1", CONTROL_URL, LISTEN_URL)
    (request,) = recorder.requests
    assert request.method == "POST" and request.url.path == "/call"
    assert request.headers["authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body["metadata"] == {"call_id": "abc"}
    assert body["phoneNumberId"] == "pn-1"
    assert body["customer"] == {"number": "+15550100"}


def test_5xx_raises_uncertain_one_request() -> None:
    recorder = Recorder(lambda r: httpx.Response(503, text="down"))
    with pytest.raises(VoiceUncertain):
        create(vapi(recorder))
    assert len(recorder.requests) == 1


def test_read_timeout_raises_uncertain_one_request() -> None:
    recorder = Recorder(raising(httpx.ReadTimeout("slow")))
    with pytest.raises(VoiceUncertain):
        create(vapi(recorder))
    assert len(recorder.requests) == 1


def test_connect_error_raises_not_sent() -> None:
    for exc in (httpx.ConnectError("refused"), httpx.ConnectTimeout("slow")):
        recorder = Recorder(raising(exc))
        with pytest.raises(VoiceNotSent):
            create(vapi(recorder))
        assert len(recorder.requests) == 1


def test_4xx_raises_rejected_with_code() -> None:
    recorder = Recorder(lambda r: httpx.Response(400, json={"code": "bad_number"}))
    with pytest.raises(VoiceRejected) as info:
        create(vapi(recorder))
    assert (info.value.status, info.value.code) == (400, "bad_number")


def test_logs_never_contain_control_url(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    adapter = vapi(Recorder(lambda r: httpx.Response(200, json={})))
    asyncio.run(adapter.send_control(CONTROL_URL, {"type": "say", "content": "hi"}))
    create(vapi(Recorder()))
    with pytest.raises(VoiceUncertain):
        asyncio.run(
            vapi(Recorder(lambda r: httpx.Response(500))).send_control(CONTROL_URL, {"type": "x"})
        )
    text = caplog.text + " ".join(str(r.args) for r in caplog.records)
    assert "secret-capability-token" not in text
    assert "secret-listen-token" not in text
    assert "test-key" not in text


def test_send_control_posts_json_without_api_key() -> None:
    recorder = Recorder(lambda r: httpx.Response(200, json={}))
    asyncio.run(vapi(recorder).send_control(CONTROL_URL, {"type": "mute"}))
    (request,) = recorder.requests
    assert str(request.url) == CONTROL_URL
    assert json.loads(request.content) == {"type": "mute"}
    assert "authorization" not in request.headers


def test_fake_records_controls() -> None:
    fake = FakeVoicePlatform()
    created = create(fake)
    assert created == CreatedCall("fake-1", "https://fake.invalid/control/1", None)
    asyncio.run(fake.send_control(created.control_url or "", {"type": "mute"}))
    assert fake.controls == [("https://fake.invalid/control/1", {"type": "mute"})]
    assert len(fake.calls) == 1


def test_fake_can_be_scripted_to_raise() -> None:
    fake = FakeVoicePlatform()
    fake.fail_next(VoiceUncertain("scripted"))
    with pytest.raises(VoiceUncertain):
        create(fake)
    assert fake.calls == []
    assert create(fake).vapi_call_id == "fake-1"


def test_find_call_by_app_id_matches_metadata() -> None:
    wanted = uuid.uuid4()
    items = [
        {"id": "other", "metadata": {"call_id": str(uuid.uuid4())}},
        {"id": "no-metadata"},
        {
            "id": "vapi-9",
            "metadata": {"call_id": str(wanted)},
            "monitor": {"controlUrl": CONTROL_URL, "listenUrl": LISTEN_URL},
        },
    ]
    recorder = Recorder(lambda r: httpx.Response(200, json=items))
    since = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    found = asyncio.run(vapi(recorder).find_call_by_app_id(wanted, since))
    assert found == CreatedCall("vapi-9", CONTROL_URL, LISTEN_URL)
    (request,) = recorder.requests
    assert request.url.path == "/call"
    assert request.url.params["createdAtGt"] == "2026-11-07T12:00:00.000Z"
    assert request.url.params["limit"] == "100"
    assert asyncio.run(vapi(recorder).find_call_by_app_id(uuid.uuid4(), since)) is None

    fake = FakeVoicePlatform()
    asyncio.run(fake.create_call(good_assistant(), "+15550100", {"call_id": str(wanted)}, SNAPSHOT))
    assert asyncio.run(fake.find_call_by_app_id(wanted, since)) == fake.calls[0]["created"]
    assert asyncio.run(fake.find_call_by_app_id(uuid.uuid4(), since)) is None


def brief(task_vars: dict[str, Any], task_line: str = "Table for {n} please.") -> dict[str, Any]:
    return {"task_line": task_line, "task_vars": task_vars}


def test_field_ref_to_ask_first_raises() -> None:
    with pytest.raises(AskFirstInPrompt) as info:
        resolve_task_vars(brief({"d": {"field_ref": "dob"}}), SNAPSHOT)
    assert info.value.key == "dob"
    assert "2004" not in str(info.value)
    assert resolve_task_vars(brief({"n": {"field_ref": "party_size"}}), SNAPSHOT) == {"n": "4"}
    with pytest.raises(MissingVar):
        resolve_task_vars(brief({"n": {"field_ref": "nope"}}), SNAPSHOT)


def test_literal_containing_ask_first_dob_blocks_create_call() -> None:
    task_vars = {"d": {"literal": "born on 2004-03-15"}}
    message = build_first_message
    with pytest.raises(AskFirstInPrompt):
        assert_prompt_safe(message(brief(task_vars, "Note {d}."), SNAPSHOT), SNAPSHOT)
    first = message(brief(task_vars, "Note {d}."), SNAPSHOT)
    recorder = Recorder()
    fake = FakeVoicePlatform()
    for adapter in (vapi(recorder), fake):
        with pytest.raises(AskFirstInPrompt):
            create(adapter, good_assistant(firstMessage=first))
    assert recorder.requests == [] and fake.calls == []


def test_opening_uses_calling_name_only() -> None:
    first = build_first_message(
        brief({"n": {"field_ref": "party_size"}}, "Table for {n} please."), SNAPSHOT
    )
    assert first == (
        "Hi, I'm an AI assistant calling on behalf of Alex. This call is transcribed. "
        "Table for 4 please."
    )
    assert "Chen" not in first
    assert_prompt_safe(first, SNAPSHOT)
    with pytest.raises(MissingVar):
        build_first_message(brief({}, "Table for {n} please."), SNAPSHOT)
    with pytest.raises(MissingVar):
        build_first_message(brief({}, "Hello."), Snapshot.from_rows([]))


def test_ask_first_full_name_equal_to_calling_name_allowed() -> None:
    snap = Snapshot.from_rows(
        [("calling_name", "Alex Chen", "share_freely"), ("full_name", "alex  CHEN", "ask_first")]
    )
    assert_prompt_safe("Calling on behalf of Alex Chen.", snap)
    snap2 = Snapshot.from_rows(
        [("calling_name", "Alex", "share_freely"), ("full_name", "Alex Chen", "ask_first")]
    )
    with pytest.raises(AskFirstInPrompt):
        assert_prompt_safe("Calling on behalf of Alex Chen.", snap2)


def test_short_ask_first_value_blocked_as_token() -> None:
    with pytest.raises(AskFirstInPrompt) as info:
        assert_prompt_safe("unit 7B please", SNAPSHOT)
    assert info.value.key == "apartment"
    assert_prompt_safe("room 17Bx please", SNAPSHOT)


def test_partial_word_overlap_not_blocked() -> None:
    assert_prompt_safe("Main Street Diner", SNAPSHOT)
    assert_prompt_safe("12 Main Street", SNAPSHOT)


def test_ask_first_contained_in_other_share_freely_field_still_blocked() -> None:
    # The share-freely "notes" value embeds the ask-first address; the value must be caught.
    with pytest.raises(AskFirstInPrompt) as info:
        assert_prompt_safe("Notes: lives at 12 Main St near the park", SNAPSHOT)
    assert info.value.key == "address"


def test_ask_first_value_in_user_role_message_blocked() -> None:
    assistant = good_assistant()
    assistant["model"]["messages"].append({"role": "user", "content": "my unit is 7b"})
    recorder = Recorder()
    fake = FakeVoicePlatform()
    for adapter in (vapi(recorder), fake):
        with pytest.raises(AskFirstInPrompt):
            create(adapter, assistant)
    assert recorder.requests == [] and fake.calls == []


def test_final_config_tools_are_fixed_and_include_end_call() -> None:
    recorder = Recorder()
    fake = FakeVoicePlatform()
    original = good_assistant()
    snapshot_of_input = copy.deepcopy(original)
    create(vapi(recorder), original)
    create(fake, original)
    assert original == snapshot_of_input  # caller's dict is not mutated
    sent = json.loads(recorder.requests[0].content)["assistant"]
    for tools in (sent["model"]["tools"], fake.calls[0]["assistant"]["model"]["tools"]):
        names = [t.get("function", {}).get("name") for t in tools]
        assert names == ["ask_user", "report_decision", "report_outcome", None]
        assert tools[-1] == {"type": "endCall"}
        assert "evil" not in json.dumps(tools)


def test_public_create_call_runs_prompt_safety() -> None:
    bad = good_assistant(firstMessage="This is for Alex Chen born 2004-03-15")
    recorder = Recorder()
    fake = FakeVoicePlatform()
    with pytest.raises(AskFirstInPrompt):
        create(vapi(recorder), bad)
    with pytest.raises(AskFirstInPrompt):
        create(fake, bad)
    assert recorder.requests == []
    assert fake.calls == [] and fake.controls == []


def test_adapter_exception_messages_never_contain_url() -> None:
    cases: list[tuple[Callable[[httpx.Request], httpx.Response], type[Exception]]] = [
        (raising(httpx.ConnectError("boom " + CONTROL_URL)), VoiceNotSent),
        (raising(httpx.ReadTimeout("boom " + CONTROL_URL)), VoiceUncertain),
        (lambda r: httpx.Response(502, text=CONTROL_URL), VoiceUncertain),
        (lambda r: httpx.Response(404, json={"code": CONTROL_URL}), VoiceRejected),
    ]
    for respond, expected in cases:
        adapter = vapi(Recorder(respond))
        with pytest.raises(expected) as info:
            asyncio.run(adapter.send_control(CONTROL_URL, {"type": "x"}))
        text = str(info.value) + repr(info.value)
        assert "capability" not in text and "https://" not in text and "test-key" not in text
        assert (
            info.value.__cause__ is None
            and info.value.__context__ is None
            or info.value.__suppress_context__
        )


def test_cjk_ask_first_value_blocked() -> None:
    snap = Snapshot.from_rows([("full_name", "王小明", "ask_first")])
    with pytest.raises(AskFirstInPrompt) as info:
        assert_prompt_safe("Calling for 王小明 today", snap)
    assert info.value.key == "full_name"
    assert_prompt_safe("Calling for someone today", snap)


def test_accented_ask_first_value_blocked_without_false_match() -> None:
    snap = Snapshot.from_rows([("address", "Müllerstraße 5", "ask_first")])
    with pytest.raises(AskFirstInPrompt):
        assert_prompt_safe("deliver to MÜLLERSTRASSE 5, please", snap)
    with pytest.raises(AskFirstInPrompt):
        assert_prompt_safe("deliver to müllerstraße 5", snap)
    assert_prompt_safe("deliver to Mueller Street 5", snap)


def test_ask_first_value_without_word_characters_fails_closed() -> None:
    snap = Snapshot.from_rows([("pin", "#!", "ask_first")])
    with pytest.raises(AskFirstInPrompt):
        assert_prompt_safe("the code is #! ok", snap)
    assert_prompt_safe("nothing here", snap)


def test_created_call_repr_has_no_urls() -> None:
    text = repr(CreatedCall("vapi-1", CONTROL_URL, LISTEN_URL))
    assert "secret-capability-token" not in text and "secret-listen-token" not in text
    assert "vapi-1" in text


def test_find_call_full_page_without_match_is_uncertain() -> None:
    items = [{"id": f"c{i}", "metadata": {"call_id": str(uuid.uuid4())}} for i in range(100)]
    recorder = Recorder(lambda r: httpx.Response(200, json=items))
    since = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    with pytest.raises(VoiceUncertain):
        asyncio.run(vapi(recorder).find_call_by_app_id(uuid.uuid4(), since))
    # A full page that does contain the call is still a match.
    wanted = uuid.uuid4()
    items[-1]["metadata"] = {"call_id": str(wanted)}
    assert asyncio.run(vapi(recorder).find_call_by_app_id(wanted, since)) is not None


@pytest.mark.parametrize("body", ["a string", 42, None])
def test_find_call_unexpected_body_is_uncertain(body: Any) -> None:
    recorder = Recorder(lambda r: httpx.Response(200, json=body))
    with pytest.raises(VoiceUncertain):
        asyncio.run(
            vapi(recorder).find_call_by_app_id(uuid.uuid4(), datetime(2026, 11, 7, tzinfo=UTC))
        )


@pytest.mark.parametrize(
    "exc",
    [
        httpx.InvalidURL("bad " + CONTROL_URL),
        httpx.TooManyRedirects("loop " + CONTROL_URL),
        httpx.DecodingError("bad body " + CONTROL_URL),
    ],
)
def test_other_httpx_errors_map_to_uncertain_without_url(exc: Exception) -> None:
    adapter = vapi(Recorder(raising(exc)))
    with pytest.raises(VoiceUncertain) as info:
        asyncio.run(adapter.send_control(CONTROL_URL, {"type": "x"}))
    assert "capability" not in str(info.value) + repr(info.value)
    assert info.value.__cause__ is None


def test_unspaced_script_ask_first_name_blocked_inside_running_text() -> None:
    snap = Snapshot.from_rows([("full_name", "王小明", "ask_first")])
    for text in ("請找王小明先生", "王小明さんの予約", "請找 王 小 明 先生"):
        with pytest.raises(AskFirstInPrompt):
            assert_prompt_safe(text, snap)
    assert_prompt_safe("請找李先生，謝謝", snap)
    assert_prompt_safe("田中さんの予約", snap)


def test_unspaced_script_calling_name_exemption_still_applies() -> None:
    snap = Snapshot.from_rows(
        [("calling_name", "王小明", "share_freely"), ("full_name", "王小明", "ask_first")]
    )
    assert_prompt_safe("請找王小明先生", snap)
