"""The brief evals (backend/evals/briefs) offline: every case against its fake LLM answer.

Same path and same checks as the live ``make eval-briefs``, through the real
``DeepSeekClient`` (request, parsing, validation) on an ``httpx.MockTransport``, so "around 10
test goals produce valid briefs" is covered without calling the API.
"""

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.briefs.llm import DeepSeekClient
from evals.briefs.checks import RecordingTransport, check_case, load_cases, run_case

PROFILE, CASES = load_cases()


def recorder_answering(answer: dict[str, Any]) -> RecordingTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(answer)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
        )

    return RecordingTransport(httpx.MockTransport(handler))


def test_there_are_around_ten_cases_within_the_chat_limits() -> None:
    assert len(CASES) >= 10
    assert len({c["id"] for c in CASES}) == len(CASES)
    for case in CASES:
        assert 1 <= len(case["goal"]) <= 300, case["id"]  # BuildRequest.goal
        assert 1 <= len(case["business_name"]) <= 100, case["id"]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case_passes_against_its_fake_answer(case: dict[str, Any]) -> None:
    recorder = recorder_answering(case["fake_response"])
    client = DeepSeekClient("sk-test-fake", "https://llm.example.test", transport=recorder)
    result = asyncio.run(run_case(case, PROFILE, client, recorder))
    assert len(result.sent) == 1
    assert check_case(case, result, PROFILE) == []


def test_the_checks_catch_a_leak_and_a_wrong_readiness() -> None:
    """The checker itself: an answer echoing the user's name is refused and reported."""
    case = next(c for c in CASES if c["id"] == "bakery_pickup")
    leaky = {**case["fake_response"], "suggested_preferences": ["Say it's for Alex Chen"]}
    recorder = recorder_answering(leaky)
    client = DeepSeekClient("sk-test-fake", "https://llm.example.test", transport=recorder)
    result = asyncio.run(run_case(case, PROFILE, client, recorder))
    failures = check_case(case, result, PROFILE)
    assert any("llm_used is False" in f for f in failures)
