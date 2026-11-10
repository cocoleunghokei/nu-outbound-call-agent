"""Pure post-call decisions: the call's outcome (PO-1) and which fields were spoken (PO-3).

No database and no network here, so every rule is unit-tested on its own. The jobs in
``jobs.py`` read the inputs, call these functions and write the results.

Rules (docs/architecture.md, post-call processing):
- Booked, Cancelled or Rescheduled count only when the agent's ``report_outcome`` says the
  business confirmed it **and** the business's own words in the final transcript back it
  up (a matching time, date or confirmation number). Otherwise: Needs follow-up.
- With no ``report_outcome``, the outcome comes from Vapi's end reason. Only a clear
  "nobody answered" becomes No answer; anything else is Needs follow-up, never a guessed
  Failed.
- A call ended by the pause cap (DC-11) is Needs follow-up.
"""

import re
from dataclasses import dataclass
from datetime import date

from app.contracts.generated import OutcomeDetails, ReportOutcomeArgs

# Outcomes that claim something was arranged, so they need the business's confirmation.
NEEDS_EVIDENCE = frozenset({"booked", "cancelled", "rescheduled"})

# Vapi end reasons that mean nobody picked up. Everything else is Needs follow-up.
NO_ANSWER_REASONS = frozenset({"customer-did-not-answer", "customer-busy", "voicemail"})

PAUSE_CAP = "pause_cap"

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)


@dataclass(frozen=True)
class OutcomeDecision:
    """The outcome to store, and why (logged; there is no column for the reason yet)."""

    outcome: str
    reason: str


def decide_outcome(
    report: ReportOutcomeArgs | None,
    ended_reason: str,
    business_lines: list[str],
    *,
    pause_capped: bool = False,
) -> OutcomeDecision:
    """Decide the call's outcome (PO-1) from the agent's report and the final transcript."""
    if pause_capped and report is None:
        return OutcomeDecision("needs_follow_up", "ended by the pause cap")
    if report is None:
        if ended_reason in NO_ANSWER_REASONS:
            return OutcomeDecision("no_answer", f"no report_outcome; ended: {ended_reason}")
        return OutcomeDecision(
            "needs_follow_up", f"no report_outcome; ended: {ended_reason or 'unknown'}"
        )

    reported = report.outcome.value
    if reported not in NEEDS_EVIDENCE:
        return OutcomeDecision(reported, "reported by the agent")
    if not report.business_confirmed:
        return OutcomeDecision("needs_follow_up", f"{reported} not confirmed by the business")
    if not has_confirmation_evidence(report.details, business_lines):
        return OutcomeDecision(
            "needs_follow_up", f"{reported} not found in the business's words in the transcript"
        )
    return OutcomeDecision(reported, "reported, confirmed and found in the transcript")


def has_confirmation_evidence(details: OutcomeDetails | None, business_lines: list[str]) -> bool:
    """True if the business said the agreed time, date or confirmation number."""
    if details is None:
        return False
    text = " ".join(business_lines).casefold()
    if not text.strip():
        return False
    if details.confirmation_number and _contains_token(text, details.confirmation_number):
        return True
    if details.time and any(_contains_token(text, t) for t in time_phrases(details.time)):
        return True
    if details.date and any(_contains_token(text, d) for d in date_phrases(details.date)):
        return True
    return False


def time_phrases(value: str) -> list[str]:
    """Ways a business might say a time. "19:30" gives "7:30" and "19:30"; "19:00" also
    gives "7pm", "7 pm" and "7 o'clock". Anything else is matched as written."""
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value)
    if not match:
        return [value.strip()] if value.strip() else []
    hour, minute = int(match[1]), int(match[2])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return [value.strip()]
    h12 = hour % 12 or 12
    phrases = [f"{h12}:{minute:02d}", f"{hour}:{minute:02d}"]
    if minute == 0:
        phrases += [
            f"{h12}pm" if hour >= 12 else f"{h12}am",
            f"{h12} pm" if hour >= 12 else f"{h12} am",
        ]
        phrases.append(f"{h12} o'clock")
    return phrases


def date_phrases(value: str) -> list[str]:
    """Ways a business might say a date. "2026-10-24" gives "saturday", "october 24",
    "oct 24" and "24th". Anything else is matched as written."""
    try:
        day = date.fromisoformat(value.strip())
    except ValueError:
        return [value.strip()] if value.strip() else []
    month = _MONTHS[day.month - 1]
    return [
        _WEEKDAYS[day.weekday()],
        f"{month} {day.day}",
        f"{month[:3]} {day.day}",
        f"{day.day}{_ordinal_suffix(day.day)}",
    ]


def spoken_fields(snapshot: dict[str, str], agent_lines: list[str]) -> list[str]:
    """Profile field keys whose value the agent said out loud (PO-3), sorted.

    ``snapshot`` maps field key to value (private: never log it). Phone-like values match on
    their digits; other values match as whole words, ignoring case. Values shorter than two
    characters are skipped, so they can't match by accident.
    """
    text = " ".join(agent_lines)
    folded = text.casefold()
    digits = re.sub(r"\D", "", text)
    spoken = []
    for key, value in snapshot.items():
        value = value.strip()
        if len(value) < 2:
            continue
        value_digits = re.sub(r"\D", "", value)
        if len(value_digits) >= 7 and len(value_digits) >= len(value) // 2:
            # Compare the national number: "+16175550142" is said as "617-555-0142".
            if value_digits[-10:] in digits:
                spoken.append(key)
        elif _contains_token(folded, value.casefold()):
            spoken.append(key)
    return sorted(spoken)


def _contains_token(text: str, phrase: str) -> bool:
    """``phrase`` appears in ``text`` (both lowercased) and is not part of a longer word or
    number, so "7:30" does not match "17:30" and "Al" does not match "Alex"."""
    phrase = phrase.strip().casefold()
    if not phrase:
        return False
    pattern = rf"(?<![\w]){re.escape(phrase)}(?![\w])"
    return re.search(pattern, text.casefold()) is not None


def _ordinal_suffix(day: int) -> str:
    if 11 <= day % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
