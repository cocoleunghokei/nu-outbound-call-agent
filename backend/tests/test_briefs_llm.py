"""LLM step for custom goals (plan Step 10, PC-3), without a database or the real API.

Every DeepSeek call here goes to an ``httpx.MockTransport``: the client's request shape, the
error mapping (timeout -> retryable, 401 -> misconfigured), strict output validation with the
fallback to the user's own words, what is (and isn't) sent, and the route wiring.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.briefs import redact_llm_key
from app.briefs.builder import build_brief
from app.briefs.interpret import (
    REASON_BAD_OUTPUT,
    REASON_NOT_CONFIGURED,
    REASON_PERSONAL_DETAIL,
    interpret_custom_goal,
)
from app.briefs.llm import (
    MAX_SUMMARY_CHARS,
    REMOVED,
    DeepSeekClient,
    GoalInterpretation,
    LlmBadOutput,
    LlmMisconfigured,
    LlmUnavailable,
    has_personal_detail,
    interpreter_from_settings,
    scrub_goal,
)
from app.briefs.router import build_response, get_goal_interpreter
from app.config import Settings
from app.main import create_app
from app.registry import AppContext, Registries
from app.secrets_registry import get_secret_registry
from tests.test_briefs_builder import FAKE_PROFILE, RESTAURANT, auth, body, draft

FAKE_KEY = "sk-test-not-a-real-key-0000"  # gitleaks:allow
BASE = "https://llm.example.test"
Handler = Callable[[httpx.Request], httpx.Response]

GOOD_ANSWER: dict[str, Any] = {
    "goal_summary": "ask if a chocolate cake can be held for pickup tomorrow",
    "suggested_constraints": ["Pickup tomorrow"],
    "suggested_preferences": [],
    "questions_to_ask_user": ["What time tomorrow?"],
}


def completion(content: Any, finish_reason: str = "stop") -> dict[str, Any]:
    text = content if isinstance(content, str) else json.dumps(content)
    return {
        "choices": [
            {"message": {"role": "assistant", "content": text}, "finish_reason": finish_reason}
        ]
    }


def answering(content: Any, status: int = 200, seen: list[httpx.Request] | None = None) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=completion(content))

    return handler


def client_for(handler: Handler, **kwargs: Any) -> DeepSeekClient:
    return DeepSeekClient(
        FAKE_KEY, BASE, "deepseek-chat", transport=httpx.MockTransport(handler), **kwargs
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------- client


def test_happy_path_sends_a_json_mode_chat_completion() -> None:
    seen: list[httpx.Request] = []
    answer = run(client_for(answering(GOOD_ANSWER, seen=seen)).interpret_goal("hold a cake"))
    assert answer == GoalInterpretation(**GOOD_ANSWER)
    (request,) = seen
    assert str(request.url) == f"{BASE}/chat/completions"
    assert request.method == "POST"
    assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    sent = json.loads(request.content)
    assert sent["model"] == "deepseek-chat"
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["temperature"] <= 0.3
    assert sent["stream"] is False
    assert json.loads(sent["messages"][1]["content"]) == {"goal": "hold a cake"}
    assert "json" in sent["messages"][0]["content"].lower()  # JSON mode needs the word


def test_a_trailing_slash_on_the_base_url_is_fine() -> None:
    seen: list[httpx.Request] = []
    c = DeepSeekClient(
        FAKE_KEY, BASE + "/", transport=httpx.MockTransport(answering(GOOD_ANSWER, seen=seen))
    )
    run(c.interpret_goal("x"))
    assert str(seen[0].url) == f"{BASE}/chat/completions"


@pytest.mark.parametrize(
    "exc", [httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow"), httpx.ConnectError("down")]
)
def test_timeouts_and_network_errors_are_retryable(exc: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    with pytest.raises(LlmUnavailable) as caught:
        run(client_for(handler).interpret_goal("x"))
    api = caught.value.api_error()
    assert (api.code, api.status, api.retryable) == ("llm_unavailable", 503, True)


def test_the_20_second_deadline_covers_the_whole_call() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=completion(GOOD_ANSWER))

    client = DeepSeekClient(FAKE_KEY, BASE, transport=httpx.MockTransport(slow), deadline_s=0.05)
    with pytest.raises(LlmUnavailable):
        run(client.interpret_goal("x"))


def test_the_default_deadline_is_20_seconds() -> None:
    assert DeepSeekClient(FAKE_KEY)._deadline_s == 20.0


def test_after_a_timeout_the_user_can_retry_and_succeed() -> None:
    """The retry path: the first try times out (retryable), the user's retry works."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, json=completion(GOOD_ANSWER))

    client = client_for(handler)
    with pytest.raises(LlmUnavailable) as caught:
        run(client.interpret_goal("x"))
    assert caught.value.retryable is True
    assert run(client.interpret_goal("x")).goal_summary == GOOD_ANSWER["goal_summary"]
    assert calls == 2  # the client never retries on its own


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_429_and_5xx_are_retryable(status: int) -> None:
    with pytest.raises(LlmUnavailable):
        run(client_for(answering({}, status=status)).interpret_goal("x"))


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404])
def test_auth_and_other_4xx_are_misconfigured_and_logged_without_the_key(
    status: int, caplog: pytest.LogCaptureFixture
) -> None:
    with (
        caplog.at_level(logging.ERROR, logger="app.briefs.llm"),
        pytest.raises(LlmMisconfigured) as caught,
    ):
        run(client_for(answering({"error": FAKE_KEY}, status=status)).interpret_goal("x"))
    api = caught.value.api_error()
    assert (api.code, api.status, api.retryable) == ("llm_misconfigured", 503, False)
    assert FAKE_KEY not in api.message
    assert str(status) in caplog.text
    assert FAKE_KEY not in caplog.text


def test_a_response_that_is_not_json_is_bad_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>oops</html>")

    with pytest.raises(LlmBadOutput):
        run(client_for(handler).interpret_goal("x"))


@pytest.mark.parametrize(
    ("content", "finish_reason"),
    [
        ("not json at all", "stop"),
        ("", "stop"),
        ("[]", "stop"),
        (json.dumps(GOOD_ANSWER), "length"),
        ({**GOOD_ANSWER, "phone_number": "+16175550142"}, "stop"),  # extra key
        ({"suggested_constraints": []}, "stop"),  # no summary
        ({**GOOD_ANSWER, "goal_summary": 7}, "stop"),  # strict: no coercion
        ({**GOOD_ANSWER, "goal_summary": " ".join(["word"] * 16)}, "stop"),
        ({**GOOD_ANSWER, "goal_summary": "a" * (MAX_SUMMARY_CHARS + 1)}, "stop"),
        ({**GOOD_ANSWER, "goal_summary": "book for {calling_name}"}, "stop"),
        ({**GOOD_ANSWER, "goal_summary": "book for [removed]"}, "stop"),
        ({**GOOD_ANSWER, "goal_summary": "I'm calling to book a table"}, "stop"),
        ({**GOOD_ANSWER, "suggested_constraints": ["a", "b", "c", "d"]}, "stop"),
        ({**GOOD_ANSWER, "suggested_preferences": ["x" * 201]}, "stop"),
        ({**GOOD_ANSWER, "questions_to_ask_user": [""]}, "stop"),
    ],
)
def test_output_that_breaks_the_schema_is_bad_output(content: Any, finish_reason: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion(content, finish_reason))

    with pytest.raises(LlmBadOutput):
        run(client_for(handler).interpret_goal("x"))


@pytest.mark.parametrize("body_json", [{}, {"choices": []}, {"choices": [{"message": {}}]}])
def test_a_completion_without_content_is_bad_output(body_json: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body_json)

    with pytest.raises(LlmBadOutput):
        run(client_for(handler).interpret_goal("x"))


def test_a_15_word_summary_of_104_characters_is_accepted() -> None:
    summary = " ".join(["abcdef"] * 14) + " " + "x" * (MAX_SUMMARY_CHARS - 14 * 7)
    assert len(summary) == MAX_SUMMARY_CHARS and len(summary.split()) == 15
    answer = run(client_for(answering({"goal_summary": summary})).interpret_goal("x"))
    assert answer.goal_summary == summary
    assert answer.suggested_constraints == []
    assert len(f"I'm calling to {summary}.") == 120


# ---------------------------------------------------------------- privacy

PROFILE_VALUES = [f.value for f in FAKE_PROFILE]


def test_scrub_removes_profile_values_phones_emails_and_full_dates() -> None:
    goal = (
        "Book a checkup for Alex Chen, DOB 2004-03-15 or 3/15/2004 or March 15, 2004, "
        "call 617-555-0142 or (617) 555 0199, email alex.chen@example.test, "
        "I live at 123 Example St, Boston, MA. Friday 3/15 at 7pm is fine."
    )
    scrubbed = scrub_goal(goal, PROFILE_VALUES)
    for leaked in ["Alex", "2004", "555", "example.test", "Example St"]:
        assert leaked not in scrubbed, leaked
    assert "Book a checkup for" in scrubbed
    assert "Friday 3/15 at 7pm is fine." in scrubbed  # a call detail, not a birthday
    assert REMOVED in scrubbed


def test_scrub_ignores_very_short_profile_values() -> None:
    assert scrub_goal("Book a table for two", ["MA", "Al"]) == "Book a table for two"


def test_has_personal_detail() -> None:
    assert has_personal_detail(["book for alex chen"], PROFILE_VALUES)
    assert has_personal_detail(["call 617 555 0199"], [])
    assert has_personal_detail(["born 2004-03-15"], [])
    assert not has_personal_detail(["book a table for 4 at 7:30pm on 3/15"], PROFILE_VALUES)


def test_only_the_scrubbed_goal_is_sent_never_profile_or_call_details() -> None:
    seen: list[httpx.Request] = []
    d = draft(
        goal="Book a checkup, my DOB is 2004-03-15 and call me back at 617-555-0142",
        business_name="Alex Chen Clinic",
        profile_additions=[("insurance_member_id", "ZX-998877")],
    )
    run(interpret_custom_goal(d, FAKE_PROFILE, client_for(answering(GOOD_ANSWER, seen=seen))))
    (request,) = seen
    sent = request.content.decode()
    assert "Book a checkup, my DOB is" in sent
    for value in [*PROFILE_VALUES, "617-555-0142", "6175550142", "ZX-998877", "Alex", d.phone_e164]:
        assert value not in sent, value
    assert d.business_name not in sent


# ---------------------------------------------------------------- interpretation and fallback


class FakeInterpreter:
    """A ``GoalInterpreter`` that records what it got and answers (or raises) as told."""

    def __init__(self, answer: dict[str, Any] | Exception | None = None) -> None:
        self.answer = GOOD_ANSWER if answer is None else answer
        self.goals: list[str] = []

    async def interpret_goal(self, goal: str) -> GoalInterpretation:
        self.goals.append(goal)
        if isinstance(self.answer, Exception):
            raise self.answer
        return GoalInterpretation.model_validate(self.answer)


def test_the_llm_summary_fills_the_custom_task_line() -> None:
    fake = FakeInterpreter({**GOOD_ANSWER, "goal_summary": "Ask if a cake can be held."})
    d = draft()
    result = run(interpret_custom_goal(d, FAKE_PROFILE, fake))
    assert (result.llm_used, result.reason) == (True, None)
    assert result.goal_summary == "ask if a cake can be held"  # same casing rule as Step 9
    assert result.suggested_constraints == ["Pickup tomorrow"]
    assert result.questions_to_ask_user == ["What time tomorrow?"]
    built = build_response(replace(d, goal_summary=result.goal_summary), FAKE_PROFILE)
    assert built["brief"]["task_line"] == "I'm calling to {goal_summary}."
    assert built["brief"]["task_vars"] == {"goal_summary": {"literal": "ask if a cake can be held"}}
    assert built["opening_line"].endswith("I'm calling to ask if a cake can be held.")
    assert built["missing_fields"] == []


def test_no_key_means_the_raw_goal_path() -> None:
    result = run(interpret_custom_goal(draft(), FAKE_PROFILE, None))
    assert (result.llm_used, result.reason) == (False, REASON_NOT_CONFIGURED)
    assert result.goal_summary == "ask if they can hold a cake for pickup tomorrow"
    assert interpreter_from_settings(Settings()) is None


def test_a_key_in_settings_makes_a_deepseek_client() -> None:
    client = interpreter_from_settings(Settings(deepseek_api_key=FAKE_KEY))  # type: ignore[arg-type]
    assert isinstance(client, DeepSeekClient)
    assert client._url == "https://api.deepseek.com/chat/completions"


def test_bad_output_falls_back_to_the_users_words() -> None:
    result = run(interpret_custom_goal(draft(), FAKE_PROFILE, FakeInterpreter(LlmBadOutput("x"))))
    assert (result.llm_used, result.reason) == (False, REASON_BAD_OUTPUT)
    assert result.goal_summary == "ask if they can hold a cake for pickup tomorrow"
    assert result.suggested_constraints == []


def test_a_summary_that_is_only_punctuation_falls_back() -> None:
    result = run(
        interpret_custom_goal(draft(), FAKE_PROFILE, FakeInterpreter({"goal_summary": "..."}))
    )
    assert result.reason == REASON_BAD_OUTPUT


def test_an_answer_with_a_profile_value_falls_back() -> None:
    fake = FakeInterpreter({**GOOD_ANSWER, "suggested_preferences": ["Ask for Alex Chen"]})
    result = run(interpret_custom_goal(draft(), FAKE_PROFILE, fake))
    assert (result.llm_used, result.reason) == (False, REASON_PERSONAL_DETAIL)


@pytest.mark.parametrize("exc", [LlmUnavailable("t"), LlmMisconfigured("k")])
def test_provider_errors_reach_the_route(exc: Exception) -> None:
    with pytest.raises(type(exc)):
        run(interpret_custom_goal(draft(), FAKE_PROFILE, FakeInterpreter(exc)))


def test_an_ask_first_value_typed_into_the_goal_still_blocks_start() -> None:
    d = draft(goal="Book a checkup, my DOB is 2004-03-15")
    result = run(
        interpret_custom_goal(d, FAKE_PROFILE, FakeInterpreter({"goal_summary": "book a checkup"}))
    )
    built = build_response(replace(d, goal_summary=result.goal_summary), FAKE_PROFILE)
    assert "date_of_birth" in built["missing_fields"]
    assert built["opening_line"] is None


def test_template_briefs_build_unchanged_without_the_llm() -> None:
    d = draft(
        template_key="restaurant_booking",
        answers={"party_size": "2", "day": "Friday", "time": "7pm"},
        goal_summary="ignored for template calls",
    )
    built = build_brief(d, RESTAURANT, FAKE_PROFILE)
    assert "goal_summary" not in built.brief["task_vars"]
    assert built.missing_fields == []


# ---------------------------------------------------------------- routes, without a database


@pytest.fixture
def app_and_fake() -> tuple[TestClient, FakeInterpreter]:
    from tests.test_auth import ISSUER, SECRET

    app = create_app(Settings(supabase_jwt_secret=SECRET, supabase_jwt_issuer=ISSUER))  # type: ignore[arg-type]
    fake = FakeInterpreter()
    app.dependency_overrides[get_goal_interpreter] = lambda: fake
    return TestClient(app), fake


def custom_body(**overrides: Any) -> dict[str, Any]:
    return body(template_key=None, goal="Ask if they can hold a cake", answers={}, **overrides)


def test_interpret_needs_auth(app_and_fake: tuple[TestClient, FakeInterpreter]) -> None:
    client, fake = app_and_fake
    response = client.post("/briefs/interpret", json=custom_body())
    assert response.status_code == 401
    assert fake.goals == []


def test_interpret_refuses_template_calls_and_never_calls_the_llm(
    app_and_fake: tuple[TestClient, FakeInterpreter],
) -> None:
    client, fake = app_and_fake
    response = client.post("/briefs/interpret", json=body(), headers=auth())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "not_a_custom_call"
    assert fake.goals == []


def test_interpret_then_needs_the_database(
    app_and_fake: tuple[TestClient, FakeInterpreter],
) -> None:
    """The rate limit and profile live in Postgres: no DATABASE_URL, the shared 503, no LLM call."""
    client, fake = app_and_fake
    response = client.post("/briefs/interpret", json=custom_body(), headers=auth())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    assert fake.goals == []


def test_build_never_calls_the_llm(app_and_fake: tuple[TestClient, FakeInterpreter]) -> None:
    client, fake = app_and_fake
    for data in (body(), custom_body(), custom_body(goal_summary="ask about cakes")):
        client.post("/briefs/build", json=data, headers=auth())
    assert fake.goals == []


@pytest.mark.parametrize(
    "data",
    [
        body(goal_summary="book a table"),  # template call
        custom_body(goal_summary=" ".join(["word"] * 16)),
        custom_body(goal_summary="book for {calling_name}"),
        custom_body(goal_summary=""),
    ],
)
def test_build_validates_goal_summary(
    app_and_fake: tuple[TestClient, FakeInterpreter], data: dict[str, Any]
) -> None:
    client, _ = app_and_fake
    response = client.post("/briefs/build", json=data, headers=auth())
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_request"


def test_the_app_starts_without_a_key() -> None:
    with TestClient(create_app(Settings())) as client:
        assert client.get("/health").status_code == 200


def test_the_startup_hook_registers_the_key_for_redaction() -> None:
    settings = Settings(deepseek_api_key="sk-test-redact-me-1234")  # type: ignore[arg-type]  # gitleaks:allow
    run(redact_llm_key(AppContext(pool=None, settings=settings, registries=Registries())))
    assert "sk-test-redact-me-1234" in get_secret_registry().all()


# ------------------------------------------------- regression: long goal on the fallback path


def test_a_goal_too_long_to_open_with_is_reported_missing() -> None:
    """Live eval found it: when the LLM's summary fails validation, the fallback used the raw
    goal and produced a 301-character opening line. Now the gap is reported instead."""
    from app.briefs.builder import (
        GOAL_SUMMARY_KEY,
        GOAL_SUMMARY_MAX_WORDS,
        Draft,
        ProfileField,
        build_brief,
        goal_summary_fits,
    )

    long_goal = (
        "My kid has a peanut allergy and we want to book a birthday party for about twelve "
        "children next month, ideally on a Saturday afternoon, with a nut-free cake"
    )
    profile = [ProfileField("calling_name", "Alex", "share_freely")]
    built = build_brief(Draft(None, long_goal, "Funtime Play Cafe", "+16175550188"), None, profile)
    assert GOAL_SUMMARY_KEY in built.missing_fields

    short = build_brief(
        Draft(
            None,
            long_goal,
            "Funtime Play Cafe",
            "+16175550188",
            goal_summary="book a nut-free birthday party",
        ),
        None,
        profile,
    )
    assert GOAL_SUMMARY_KEY not in short.missing_fields
    assert not goal_summary_fits(" ".join(["word"] * (GOAL_SUMMARY_MAX_WORDS + 1)))
    assert goal_summary_fits("ask if they can hold a cake")
