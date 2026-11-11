"""Shared by the live eval (``run.py``) and the offline test (tests/test_briefs_llm_evals.py).

Each case is a custom-call goal. ``run_case`` sends it through the same path as
``POST /briefs/interpret`` (scrub, LLM, validation, fallback, build, readiness) and
``check_case`` lists every way the result breaks the rules:

- the brief is valid ``brief.v1``, ``task_line`` is at most 120 characters, and so is the
  resolved custom line "I'm calling to {goal_summary}."
- the summary is at most 15 words with no braces or brackets (DC-1)
- no profile value, phone number, email or full date in what was sent to the LLM, in the
  interpretation or in the brief's ``task_vars``
- readiness matches the case (``ready``, ``missing_includes``), and so do ``llm_used``,
  ``summary_any_of`` and ``min_questions``.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx

from app.briefs.builder import CUSTOM_TASK_LINE, Draft, ProfileField
from app.briefs.interpret import Interpretation, interpret_custom_goal, private_values
from app.briefs.llm import (
    MAX_SUMMARY_WORDS,
    GoalInterpreter,
    LlmError,
    has_personal_detail,
)
from app.briefs.router import build_response
from app.contracts import validator_for

CASES_PATH = Path(__file__).with_name("cases.json")
EVAL_PHONE = "+16175550122"  # fake business number


def load_cases() -> tuple[list[ProfileField], list[dict[str, Any]]]:
    """The fake profile and the cases from cases.json."""
    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    profile = [ProfileField(p["key"], p["value"], p["sharing"]) for p in data["profile"]]
    return profile, list(data["cases"])


class RecordingTransport(httpx.AsyncBaseTransport):
    """Wraps a transport and keeps every request body, so checks can see what was sent."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.bodies: list[bytes] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(await request.aread())
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


@dataclass
class CaseResult:
    """One case's outcome: the build response, or the LLM error that stopped it."""

    case_id: str
    response: dict[str, Any] | None = None
    interpretation: Interpretation | None = None
    error: str | None = None
    sent: list[bytes] = field(default_factory=list)


def draft_for(case: dict[str, Any]) -> Draft:
    """The custom-call draft for a case."""
    return Draft(
        template_key=None,
        goal=case["goal"],
        business_name=case["business_name"],
        phone_e164=EVAL_PHONE,
    )


async def run_case(
    case: dict[str, Any],
    profile: Sequence[ProfileField],
    interpreter: GoalInterpreter,
    recorder: RecordingTransport,
) -> CaseResult:
    """Run one case like ``POST /briefs/interpret`` does, minus HTTP and the database."""
    start = len(recorder.bodies)
    draft = draft_for(case)
    result = CaseResult(case_id=case["id"])
    try:
        interpretation = await interpret_custom_goal(draft, profile, interpreter)
    except LlmError as exc:
        result.error = exc.code
    else:
        result.interpretation = interpretation
        result.response = build_response(
            replace(draft, goal_summary=interpretation.goal_summary), profile
        )
    result.sent = recorder.bodies[start:]
    return result


def _sent_goal(body: bytes) -> str:
    return str(json.loads(body)["messages"][1]["content"])


def check_case(
    case: dict[str, Any], result: CaseResult, profile: Sequence[ProfileField]
) -> list[str]:
    """Every rule the result breaks, as plain sentences. Empty means the case passed."""
    failures: list[str] = []
    private = private_values(profile, draft_for(case))
    expect: dict[str, Any] = case["expect"]

    for body in result.sent:
        if any(v in body.decode() for v in private if len(v) >= 3):
            failures.append("a profile value was sent to the LLM")
        if has_personal_detail([_sent_goal(body)], private):
            failures.append("a personal detail was sent to the LLM")

    if result.error is not None or result.response is None or result.interpretation is None:
        return [*failures, f"LLM call failed: {result.error}"]
    interp, response = result.interpretation, result.response
    brief = response["brief"]

    errors = list(validator_for("brief.v1.json").iter_errors(brief))
    if errors:
        failures.append(f"brief is not valid brief.v1: {errors[0].message}")
    if len(brief["task_line"]) > 120:
        failures.append("task_line is over 120 characters")
    resolved = CUSTOM_TASK_LINE.replace("{goal_summary}", interp.goal_summary)
    if len(resolved) > 120:
        failures.append(f"resolved task line is {len(resolved)} characters (max 120)")
    if interp.llm_used and len(interp.goal_summary.split()) > MAX_SUMMARY_WORDS:
        failures.append(f"goal_summary is over {MAX_SUMMARY_WORDS} words")
    if any(c in interp.goal_summary for c in "{}[]"):
        failures.append("goal_summary holds braces or brackets")

    literals = [str(v.get("literal", "")) for v in brief["task_vars"].values()]
    interp_texts = [
        interp.goal_summary,
        *interp.suggested_constraints,
        *interp.suggested_preferences,
        *interp.questions_to_ask_user,
    ]
    if interp.llm_used and has_personal_detail([*interp_texts, *literals], private):
        failures.append("a personal detail is in the interpretation or task_vars")

    if interp.llm_used != expect.get("llm_used", True):
        failures.append(f"llm_used is {interp.llm_used} (reason {interp.reason})")
    ready = not response["missing_fields"] and response["opening_line"] is not None
    if ready != expect.get("ready", True):
        failures.append(f"ready is {ready} (missing {response['missing_fields']})")
    for key in expect.get("missing_includes", []):
        if key not in response["missing_fields"]:
            failures.append(f"{key} should be missing")
    words = expect.get("summary_any_of")
    if words and not any(w.lower() in interp.goal_summary.lower() for w in words):
        failures.append(f"goal_summary {interp.goal_summary!r} has none of {words}")
    if len(interp.questions_to_ask_user) < expect.get("min_questions", 0):
        failures.append("expected at least one question for the user")
    return failures
