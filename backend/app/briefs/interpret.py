"""PC-3 for custom calls: the user's plain-words goal -> goal summary and suggestions.

The LLM (``app/briefs/llm.py``) is optional. Without a key, or when its answer fails
validation or holds a personal detail, the result falls back to the user's own words
(``goal_summary(goal)``, Step 9) with ``llm_used: false`` and a reason, so the chat asks the
user to check the line instead of trusting a guess. Timeouts and provider errors are not
swallowed here: they reach the route as ``LlmUnavailable`` (retryable) or
``LlmMisconfigured``.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.briefs.builder import Draft, ProfileField, goal_summary
from app.briefs.llm import (
    GoalInterpreter,
    LlmBadOutput,
    check_goal_summary,
    has_personal_detail,
    scrub_goal,
)

REASON_NOT_CONFIGURED = "llm_not_configured"
REASON_BAD_OUTPUT = "llm_bad_output"
REASON_PERSONAL_DETAIL = "llm_personal_detail"


@dataclass(frozen=True)
class Interpretation:
    """What the chat shows for a custom goal. ``reason`` is set only when ``llm_used`` is
    false. Suggestions are offers for PC-5; they go into the brief only if the user picks
    them and sends them back in ``constraints`` / ``preferences``."""

    llm_used: bool
    reason: str | None
    goal_summary: str
    suggested_constraints: list[str] = field(default_factory=list)
    suggested_preferences: list[str] = field(default_factory=list)
    questions_to_ask_user: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        """The ``interpretation`` object of the ``POST /briefs/interpret`` response."""
        return {
            "llm_used": self.llm_used,
            "reason": self.reason,
            "goal_summary": self.goal_summary,
            "suggested_constraints": list(self.suggested_constraints),
            "suggested_preferences": list(self.suggested_preferences),
            "questions_to_ask_user": list(self.questions_to_ask_user),
        }


def private_values(profile: Sequence[ProfileField], draft: Draft) -> list[str]:
    """Every value the LLM must never see or echo: the profile and the chat's additions."""
    return [f.value for f in profile] + [value for _, value in draft.profile_additions]


def fallback(draft: Draft, reason: str) -> Interpretation:
    """The user's own words as the summary (Step 9's raw-goal path)."""
    return Interpretation(llm_used=False, reason=reason, goal_summary=goal_summary(draft.goal))


async def interpret_custom_goal(
    draft: Draft, profile: Sequence[ProfileField], interpreter: GoalInterpreter | None
) -> Interpretation:
    """Ask the LLM for a goal summary and suggestions, or fall back to the user's words.

    Sends only the scrubbed goal (``scrub_goal``). The answer is rejected (fallback) when it
    fails validation or holds a profile value, phone number, email or full date. Raises
    ``LlmUnavailable`` / ``LlmMisconfigured`` from the interpreter unchanged.
    """
    if interpreter is None:
        return fallback(draft, REASON_NOT_CONFIGURED)
    private = private_values(profile, draft)
    try:
        answer = await interpreter.interpret_goal(scrub_goal(draft.goal, private))
        # Same casing and punctuation rule as the raw-goal path, then the DC-1 limits again.
        summary = check_goal_summary(goal_summary(answer.goal_summary))
    except (LlmBadOutput, ValueError):
        return fallback(draft, REASON_BAD_OUTPUT)
    if has_personal_detail(answer.texts(), private):
        return fallback(draft, REASON_PERSONAL_DETAIL)
    return Interpretation(
        llm_used=True,
        reason=None,
        goal_summary=summary,
        suggested_constraints=list(answer.suggested_constraints),
        suggested_preferences=list(answer.suggested_preferences),
        questions_to_ask_user=list(answer.questions_to_ask_user),
    )
