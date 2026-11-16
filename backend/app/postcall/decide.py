"""Pure post-call decisions: the call's outcome (PO-1) and which fields were spoken (PO-3).

No database and no network here, so every rule is unit-tested on its own. The jobs in
``jobs.py`` read the inputs, call these functions and write the results.

Rules (docs/architecture.md, post-call processing):
- Booked, Cancelled or Rescheduled count only when the agent's ``report_outcome`` says the
  business confirmed it **and** the business's own words back it up: the agreed time
  together with a specific date (month and day), or the confirmation number. Lines where
  the business refuses ("full", "sorry", "no availability", ...) are not evidence.
  Otherwise: Needs follow-up.
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

# A business line containing any of these is a refusal or a problem, never a confirmation.
REFUSAL_WORDS = (
    "full",
    "sorry",
    "no availability",
    "not available",
    "unavailable",
    "can't",
    "cannot",
    "can not",
    "don't have",
    "do not have",
    "no openings",
    "no tables",
    "booked up",
    "not possible",
)

# Profile fields that identify the user. Only these are checked for "spoken" (PO-3), so a
# value like dietary_restrictions="None" can't match the agent saying "none".
SPOKEN_FIELD_KEYS = frozenset(
    {
        "calling_name",
        "full_name",
        "phone",
        "email",
        "date_of_birth",
        "address",
        "insurance_carrier",
        "insurance_member_id",
    }
)

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


def is_refusal(line: str) -> bool:
    """True if the business line refuses or reports a problem ("Sorry, 7:30 is full")."""
    folded = line.casefold().replace("’", "'")
    return any(_contains_token(folded, word) for word in REFUSAL_WORDS)


def clauses(line: str) -> list[str]:
    """Split a line into the parts a refusal applies to: at sentence ends and at "but".

    "7:30 is full, but 8pm on October 24 works." gives a refusal and a confirmation, so the
    confirmation still counts. A period inside "p.m." or "Oct." is not followed by a space
    and a new sentence, so it stays (the joined text keeps the words in order).
    """
    parts = re.split(r"(?<=[.!?;])\s+|,?\s+\bbut\b\s+", line)
    return [part for part in parts if part.strip()]


def has_confirmation_evidence(details: OutcomeDetails | None, business_lines: list[str]) -> bool:
    """True if the business, in a part of a line that isn't a refusal, said the confirmation
    number, or said both the agreed time and the agreed date (month and day)."""
    if details is None:
        return False
    kept = [
        part.casefold() for line in business_lines for part in clauses(line) if not is_refusal(part)
    ]
    if not kept:
        return False
    text = " ".join(kept)
    if details.confirmation_number and _contains_token(text, details.confirmation_number):
        return True
    if not details.time or not details.date:
        return False
    said_time = mentions_time(text, details.time)
    said_date = mentions_date(text, details.date)
    return said_time and said_date


def parse_time(value: str) -> tuple[int, int] | None:
    """Read "19:30", "7:30 PM", "7:30pm", "7pm" or "7 p.m." as (hour 0-23, minute)."""
    match = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\s*", value.casefold())
    if not match:
        return None
    hour, minute = int(match[1]), int(match[2] or 0)
    meridiem = match[3]
    if meridiem is None and match[2] is None:
        return None  # a bare number like "7" is not a time
    if meridiem is not None:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem.startswith("p") else 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def mentions_time(text: str, value: str) -> bool:
    """True if ``text`` (lowercased) says the time in ``value``: "7:30", "7:30pm",
    "19:30", and for whole hours also "7pm", "7 p.m." or "7 o'clock". "7:30" never
    matches inside "17:30"."""
    parsed = parse_time(value)
    if parsed is None:
        return _contains_token(text, value)
    hour, minute = parsed
    h12 = hour % 12 or 12
    numbers = {f"{h12}:{minute:02d}", f"{hour}:{minute:02d}"}
    for number in numbers:
        if re.search(rf"(?<![\d:]){re.escape(number)}(?!\d)", text):
            return True
    if minute == 0:
        meridiem = "p" if hour >= 12 else "a"
        if re.search(rf"(?<![\d:]){h12}\s?{meridiem}\.?m\b", text):
            return True
        if re.search(rf"(?<![\d:]){h12} o'clock", text.replace("’", "'")):
            return True
    return False


def mentions_date(text: str, value: str) -> bool:
    """True if ``text`` (lowercased) names the specific date in ``value`` ("2026-10-24"):
    "october 24", "oct 24th", "24th of october" or "the 24th of october". A bare weekday
    or ordinal is not enough, because "Saturday" or "the 24th" can refer to another date."""
    try:
        day = date.fromisoformat(value.strip())
    except ValueError:
        return _contains_token(text, value)
    month = _MONTHS[day.month - 1]
    months = rf"(?:{month}|{month[:3]}\.?)"
    number = rf"{day.day}(?:st|nd|rd|th)?"
    patterns = (
        rf"\b{months}\s+(?:the\s+)?{number}(?!\d)",  # october 24, oct 24th
        rf"(?<!\d){number}\s+(?:of\s+)?{months}\b",  # 24th of october, 24 october
    )
    return any(re.search(pattern, text) for pattern in patterns)


def spoken_fields(snapshot: dict[str, str], agent_lines: list[str]) -> list[str]:
    """Identifying profile fields whose value the agent said out loud (PO-3), sorted.

    ``snapshot`` maps field key to value (private: never log it). Only keys in
    ``SPOKEN_FIELD_KEYS`` are checked. A phone-like value matches one number the agent said
    (compared by its last 10 digits, so "+16175550142" matches "617-555-0142"); other
    values match as whole words, ignoring case. Values shorter than two characters are
    skipped.
    """
    text = " ".join(agent_lines)
    folded = text.casefold()
    said_numbers = [re.sub(r"\D", "", span) for span in re.findall(r"\+?\d[\d\s().-]*\d", text)]
    spoken = []
    for key, value in snapshot.items():
        value = value.strip()
        if key not in SPOKEN_FIELD_KEYS or len(value) < 2:
            continue
        value_digits = re.sub(r"\D", "", value)
        if len(value_digits) >= 7 and len(value_digits) >= len(value) // 2:
            national = value_digits[-10:]
            if any(national in said for said in said_numbers):
                spoken.append(key)
        elif _contains_token(folded, value.casefold()):
            spoken.append(key)
    return sorted(spoken)


def _contains_token(text: str, phrase: str) -> bool:
    """``phrase`` appears in ``text`` (both lowercased) and is not part of a longer word or
    number, so "Al" does not match "Alex"."""
    phrase = phrase.strip().casefold()
    if not phrase:
        return False
    pattern = rf"(?<![\w]){re.escape(phrase)}(?![\w])"
    return re.search(pattern, text.casefold()) is not None
