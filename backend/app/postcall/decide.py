"""Pure post-call decisions: the call's outcome (PO-1) and which fields were spoken (PO-3).

No database and no network here, so every rule is unit-tested on its own. The jobs in
``jobs.py`` read the inputs, call these functions and write the results.

Outcome rules (docs/architecture.md, post-call processing). When in doubt: Needs follow-up.

- Booked, Cancelled or Rescheduled count only when the agent's ``report_outcome`` says the
  business confirmed it **and** one clause of the business's own words backs it up: the
  confirmation number, or the agreed time and the agreed date together. The date is the
  month and day ("October 24"), or a weekday ("Thursday") that can only mean the agreed
  date: see ``call_days`` and ``mentions_weekday``.
- The agent's details must be well-formed to count: an ISO date (YYYY-MM-DD), a time that
  parses, a confirmation number of at least 4 letters or digits. The contract doesn't
  enforce these (one odd field must not throw away the whole report), so they are checked
  here.
- The confirmation number counts only with a cue in the same clause ("confirmation",
  "reference", "number is", ...) and never inside a longer number ("555-1234", a year).
- A clause that is a question ("October 24 at 7:30?") is never evidence. A line where any
  clause refuses ("full", "sorry", "not", "let me check", ...) is never evidence, and
  neither is anything said before a later refusal ("So October 24 at 7:30." then "No, that
  doesn't work."). A refusal before the confirmation is fine. So a counteroffer in the same
  line as a refusal is Needs follow-up: the user checks.
- Known limit: an offer from the business ("How about October 24 at 7:30.") reads like a
  confirmation. The agent's ``business_confirmed`` is the guard for that case.
- A bare "yes" counts too, when it answers the agent's proposal (see ``affirmed_proposals``):
  Agent "Would 7:30 work instead?", business "Yeah. Sure." The business agreed to what the
  agent just said, so the agent's clause is evidence, held to the same time-and-date test.
  Only a line made of nothing but agreement words counts ("Yeah, but we only have salad"
  doesn't), only straight after an agent line, and not before a later refusal.
- With no ``report_outcome``, the outcome comes from Vapi's end reason. Only a clear
  "nobody answered" becomes No answer; anything else is Needs follow-up, never a guessed
  Failed.
- A call ended by the pause cap (DC-11) is Needs follow-up.
"""

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.contracts.generated import OutcomeDetails, ReportOutcomeArgs

# One line of the call: who spoke ("agent" or "business") and what was said.
Turn = tuple[str, str]

# A line that is only agreement: "Yeah. Sure.", "Okay, sounds good!", "That works." Anything
# more ("Yeah, but we only have salad") is not a plain yes and never counts as one.
_AGREEMENT = (
    r"yes|yeah|yep|yup|sure|ok|okay|alright|all right|absolutely|perfect|great|fine|"
    r"sounds good|that works|that's fine|that is fine|works for me|that's great"
)
PLAIN_AGREEMENT = re.compile(rf"^\W*(?:(?:{_AGREEMENT})\b[\s,.!]*)+$", re.IGNORECASE)

# Outcomes that claim something was arranged, so they need the business's confirmation.
NEEDS_EVIDENCE = frozenset({"booked", "cancelled", "rescheduled"})

# Vapi end reasons that mean nobody picked up. Everything else is Needs follow-up.
NO_ANSWER_REASONS = frozenset({"customer-did-not-answer", "customer-busy", "voicemail"})

PAUSE_CAP = "pause_cap"

# A clause containing any of these (as whole words) is a refusal or a problem.
REFUSAL_WORDS = (
    "full",
    "fully",
    "sorry",
    "unfortunately",
    "nothing",
    "closed",
    "taken",
    "waitlist",
    "wait list",
    "booked solid",
    "booked up",
    "no availability",
    "not available",
    "unavailable",
    "not open",
    "not possible",
    "no openings",
    "no tables",
    "no reservation",
    "can't",
    "cant",
    "cannot",
    "can not",
    "can't find",
    "won't",
    "wont",
    "doesn't work",
    "isn't",
    "don't have",
    "dont have",
    "do not have",
    "not",
    "couldn't",
    "couldnt",
    "could not",
    "no longer",
    "gone",
    "no record",
    "call you back",
    "let me check",
)

# Corrections that start with "no" ("No, that doesn't work", "Wait, no, October 25"). A bare
# "no" is not a refusal word, so "No problem, see you October 24 at 7:30" can still confirm.
REFUSAL_PATTERNS = (
    r"^no\b\s*[,.!]",
    r"\b(?:wait|actually|oh|sorry|um|uh),?\s+no\b",
)

# One of these must be in the same clause as the confirmation number.
CONFIRMATION_CUES = (
    "confirmation",
    "reference",
    "booking number",
    "number is",
    "conf",
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

# Single-word names that are also ordinary words ("May I...", "al dente"). The agent saying
# one counts only right after a name cue such as "on behalf of" or "the name is".
AMBIGUOUS_NAMES = frozenset(
    {
        "al",
        "april",
        "art",
        "august",
        "bill",
        "dawn",
        "eve",
        "faith",
        "grace",
        "hope",
        "joy",
        "june",
        "mark",
        "may",
        "pat",
        "ray",
        "rose",
        "sue",
        "summer",
        "will",
    }
)
NAME_CUES = r"(?:behalf of|name is|name's|this is|under|for|it's)"

_STREET_WORDS = {
    "st": "street",
    "ave": "avenue",
    "rd": "road",
    "blvd": "boulevard",
    "dr": "drive",
    "ln": "lane",
    "ct": "court",
    "pl": "place",
    "apt": "apartment",
}

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

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# No time zone is stored for a call, and every call is to a US number. US local time runs
# from UTC-4 (Eastern daylight time) to UTC-10 (Hawaii), so these bound the call's local day.
_US_UTC_OFFSETS = (timedelta(hours=-4), timedelta(hours=-10))

# A weekday counts for a date 1 to 6 days after the call day. The call day's own weekday
# ("Tuesday" said on a Tuesday) could mean today or next week, so it never counts.
_MAX_WEEKDAY_AHEAD = 6

_MIN_CONFIRMATION_LENGTH = 4


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
    call_days: Collection[date] = (),
    turns: Sequence[Turn] = (),
) -> OutcomeDecision:
    """Decide the call's outcome (PO-1) from the agent's report and the final transcript.

    ``call_days`` are the local days the call could have happened on (``call_days_for``).
    Without them a weekday is never enough to confirm a date. ``turns`` is the whole call in
    order; without it a plain "yes" from the business can't be matched to what it answered.
    """
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
    if not has_confirmation_evidence(
        report.details, business_lines, call_days=call_days, turns=turns
    ):
        return OutcomeDecision(
            "needs_follow_up", f"{reported} not found in the business's words in the transcript"
        )
    return OutcomeDecision(reported, "reported, confirmed and found in the transcript")


def clauses(line: str) -> list[str]:
    """Split a line at sentence ends: any "?", or ".", "!" or ";" followed by a space and a
    capital letter. "Oct. 24" and "8 p.m. on" stay whole, because no capital follows.
    Speech-to-text often drops the capital after a question, so "?" always ends one."""
    parts = re.split(r"(?<=\?)\s*|(?<=[.!;])\s+(?=[A-Z])", line.strip())
    return [part.strip() for part in parts if part and part.strip()]


def _fold(text: str) -> str:
    return text.casefold().replace("’", "'")


def is_refusal(clause: str) -> bool:
    """True if the clause refuses or reports a problem ("Sorry, 7:30 is full")."""
    folded = _fold(clause).strip()
    if any(_contains_token(folded, word) for word in REFUSAL_WORDS):
        return True
    return any(re.search(pattern, folded) for pattern in REFUSAL_PATTERNS)


def is_question(clause: str) -> bool:
    """True if the clause asks something ("October 24 at 7:30?"): never evidence."""
    return clause.rstrip().endswith("?")


def valid_iso_date(value: str | None) -> date | None:
    """The date if ``value`` is exactly YYYY-MM-DD and a real date, else None."""
    if not value or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def valid_confirmation_number(value: str | None) -> str | None:
    """The number if it has at least 4 letters or digits, else None ("24" or "a" would match
    almost any line). Letters-only codes ("ABCD") are real; the cue rule in
    ``says_confirmation_number`` keeps them from matching ordinary words."""
    if value is None:
        return None
    value = value.strip()
    if len(re.sub(r"[^0-9A-Za-z]", "", value)) < _MIN_CONFIRMATION_LENGTH:
        return None
    return value


def says_confirmation_number(text: str, number: str) -> bool:
    """True if ``text`` (one lowercased clause) has a confirmation cue and says ``number`` on
    its own: not inside a longer number or word, and not next to "-" or "/". So "Our number
    is 555-1234." does not say 1234, and a year or a street number has no cue."""
    if not any(_contains_token(text, cue) for cue in CONFIRMATION_CUES):
        return False
    pattern = rf"(?<![\w/-]){re.escape(_fold(number))}(?![\w/]|-\w)"
    return re.search(pattern, text) is not None


def has_confirmation_evidence(
    details: OutcomeDetails | None,
    business_lines: list[str],
    *,
    call_days: Collection[date] = (),
    turns: Sequence[Turn] = (),
) -> bool:
    """True if one clause of the business's words says the confirmation number (with a cue),
    or says both the agreed time and the agreed date (``mentions_date``, or
    ``mentions_weekday`` for the possible ``call_days``). Questions, lines with a refusal and
    anything said before a later refusal line don't count.

    A plain "yes" to the agent's proposal also counts (``affirmed_proposals``): the proposal
    must say the agreed time, and the agreed date too, in its own clause or, when the clause
    names no other date, anywhere the agent said it earlier in the call."""
    if details is None:
        return False
    number = valid_confirmation_number(details.confirmation_number)
    agreed_day = valid_iso_date(details.date)
    agreed_time = parse_time(details.time) if details.time else None
    if number is None and (agreed_day is None or agreed_time is None):
        return False
    for text in evidence_clauses(business_lines):
        if number is not None and says_confirmation_number(text, number):
            return True
        if agreed_day is not None and agreed_time is not None:
            if mentions_time(text, agreed_time) and (
                mentions_date(text, agreed_day) or mentions_weekday(text, agreed_day, call_days)
            ):
                return True
    if agreed_day is not None and agreed_time is not None:
        for clause, earlier in affirmed_proposals(turns):
            if not mentions_time(clause, agreed_time):
                continue
            if _says_the_date(clause, agreed_day, call_days):
                return True
            if not _names_another_date(clause) and _says_the_date(earlier, agreed_day, call_days):
                return True
    return False


def _says_the_date(text: str, agreed: date, call_days: Collection[date]) -> bool:
    return mentions_date(text, agreed) or mentions_weekday(text, agreed, call_days)


def _names_another_date(text: str) -> bool:
    """True if ``text`` (lowercased) names any month and day or any weekday."""
    return _names_a_month_day(text) or any(re.search(rf"\b{day}\b", text) for day in _WEEKDAYS)


def affirmed_proposals(turns: Sequence[Turn]) -> list[tuple[str, str]]:
    """What the business agreed to with a plain "yes", as (clause, earlier agent words).

    A business line made only of agreement words ("Yeah. Sure.") that comes straight after an
    agent line, with no business refusal on or after it, means the business agreed to what the
    agent just said. Each clause of that agent line (questions included: "Would 7:30 work?")
    is returned lowercased, with everything the agent said up to and including that line, so
    the caller can look for the date there. A clause that is itself a refusal ("let me check")
    is left out.
    """
    refusal_after = [False] * (len(turns) + 1)
    for index in range(len(turns) - 1, -1, -1):
        role, text = turns[index]
        refuses = role == "business" and any(is_refusal(part) for part in clauses(text))
        refusal_after[index] = refusal_after[index + 1] or refuses
    found: list[tuple[str, str]] = []
    said_by_agent: list[str] = []
    for index, (role, text) in enumerate(turns):
        if role == "agent":
            said_by_agent.append(_fold(text))
            continue
        if index == 0 or turns[index - 1][0] != "agent" or refusal_after[index]:
            continue
        if not PLAIN_AGREEMENT.match(text.strip()):
            continue
        earlier = " ".join(said_by_agent)
        found.extend(
            (_fold(part), earlier) for part in clauses(turns[index - 1][1]) if not is_refusal(part)
        )
    return found


def evidence_clauses(business_lines: list[str]) -> list[str]:
    """The business's clauses that can count as evidence, lowercased, in call order.

    Left out: questions ("October 24 at 7:30?"), and every clause of a line that refuses or of
    any line before a later refusal ("So October 24 at 7:30." then "No, that doesn't work.").
    When in doubt, a clause doesn't count.
    """
    split_lines = [clauses(line) for line in business_lines]
    refuses = [any(is_refusal(part) for part in parts) for parts in split_lines]
    kept: list[str] = []
    for index, parts in enumerate(split_lines):
        if any(refuses[index:]):
            continue
        kept.extend(_fold(part) for part in parts if not is_question(part))
    return kept


def confirmed_details(
    details: OutcomeDetails | None,
    business_lines: list[str],
    *,
    call_days: Collection[date] = (),
    turns: Sequence[Turn] = (),
) -> OutcomeDetails:
    """Only the details the business itself said (PO-2), each checked on its own.

    ``has_confirmation_evidence`` needs one proof that the result happened; this decides which
    details may be shown as agreed. Each must be well-formed and said in a clause from
    ``evidence_clauses``: the date (``mentions_date`` or ``mentions_weekday``), the time, the
    confirmation number next to a cue, and the staff name as whole words (a title such as
    "Dr." may be left out). The date and the time may be said in different clauses. Anything
    else is dropped, so a detail the agent made up never shows as confirmed. What the business
    agreed to with a plain "yes" (``affirmed_proposals``) counts as said by the business.
    """
    if details is None:
        return OutcomeDetails()
    texts = evidence_clauses(business_lines) + [clause for clause, _ in affirmed_proposals(turns)]
    agreed_day = valid_iso_date(details.date)
    agreed_time = parse_time(details.time) if details.time else None
    number = valid_confirmation_number(details.confirmation_number)
    return OutcomeDetails(
        date=details.date
        if agreed_day is not None
        and any(
            mentions_date(t, agreed_day) or mentions_weekday(t, agreed_day, call_days)
            for t in texts
        )
        else None,
        time=details.time
        if agreed_time is not None and any(mentions_time(t, agreed_time) for t in texts)
        else None,
        confirmation_number=details.confirmation_number
        if number is not None and any(says_confirmation_number(t, number) for t in texts)
        else None,
        staff_name=details.staff_name
        if details.staff_name and any(says_staff_name(t, details.staff_name) for t in texts)
        else None,
    )


_TITLES = r"(?:dr|doctor|mr|mrs|ms|miss|nurse)\.?"


def says_staff_name(text: str, name: str) -> bool:
    """True if ``text`` (one lowercased clause) says ``name`` as whole words, ignoring case.
    A leading title may be left out on either side: "Dr. Rivera" matches "Rivera" and
    "Doctor Rivera". A name shorter than two letters never matches."""
    bare = re.sub(rf"^{_TITLES}\s+", "", _fold(name).strip())
    if len(re.sub(r"[^a-z]", "", bare)) < 2:
        return False
    return _contains_token(text, bare)


def parse_time(value: str | None) -> tuple[int, int] | None:
    """Read "19:30", "7:30 PM", "7:30pm", "7pm" or "7 p.m." as (hour 0-23, minute)."""
    if value is None:
        return None
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


_MERIDIEM = r"\s*(a\.?m\.?|p\.?m\.?)?"


def mentions_time(text: str, agreed: tuple[int, int]) -> bool:
    """True if ``text`` (lowercased) says the agreed (hour, minute): "7:30", "7:30pm",
    "19:30", and for whole hours also "7pm" or "7 o'clock". An am/pm that contradicts the
    agreed time ("7:30 am" for 19:30) doesn't count, and "7:30" never matches inside
    "17:30". A whole hour also counts as "at 7" with no am/pm, as people book."""
    hour, minute = agreed
    h12 = hour % 12 or 12
    wanted = "p" if hour >= 12 else "a"
    for number, is_24h in ((f"{h12}:{minute:02d}", False), (f"{hour}:{minute:02d}", True)):
        for match in re.finditer(rf"(?<![\d:]){re.escape(number)}(?!\d){_MERIDIEM}", text):
            meridiem = match.group(1)
            if meridiem is None or (not is_24h and meridiem[0] == wanted):
                return True
    else:
        # Speech-to-text often writes "7 30 PM" for 7:30 PM: only with an am/pm, so "7 30" in
        # "table for 7 30 people" is not a time.
        if re.search(rf"(?<![\d:]){h12}\s{minute:02d}\s?{wanted}\.?m\b", text):
            return True
    if minute == 0:
        if re.search(rf"(?<![\d:]){h12}\s?{wanted}\.?m\b", text):
            return True
        if re.search(rf"(?<![\d:]){h12} o'clock", text):
            return True
        # "at 2" with no am/pm, the way people book: the hour must follow "at" and nothing
        # that makes it a longer number, a time with minutes or a date ("at 2 on the 3rd" is
        # fine, "at 2:15", "at 20" and "at 2/3" are not). The agent's report decides the half
        # of the day; an am/pm said after it is handled above.
        if re.search(rf"\bat {h12}(?![\d:/]|\.\d|\s?[ap]\.?m\b)", text):
            return True
    return False


def _date_patterns(day: date) -> tuple[str, str]:
    month = _MONTHS[day.month - 1]
    months = rf"(?:{month}|{month[:3]}\.?)"
    number = rf"{day.day}(?:st|nd|rd|th)?"
    return (
        rf"\b{months}\s+(?:the\s+)?{number}(?!\d)",  # october 24, oct 24th
        rf"(?<!\d){number}\s+(?:of\s+)?{months}\b",  # 24th of october, 24 october
    )


def mentions_date(text: str, agreed: date) -> bool:
    """True if ``text`` (lowercased) names the agreed date: "october 24", "oct 24th",
    "oct. 24", "24th of october" or "the 24th of october". A bare weekday or ordinal is not
    enough, because "Saturday" or "the 24th" can refer to another date. A year said right
    after it must be the agreed one ("October 24, 2027" is not 2026-10-24)."""
    for pattern in _date_patterns(agreed):
        for match in re.finditer(pattern + r"(?:,?\s+(\d{4})\b)?", text):
            if match.group(1) is None or int(match.group(1)) == agreed.year:
                return True
    return False


def call_days_for(started_at: datetime, ended_at: datetime) -> frozenset[date]:
    """Every local day a US call between ``started_at`` and ``ended_at`` (both timezone-aware)
    could have been on, from the earliest US time zone to the latest. Usually one day; two
    when the call was near midnight somewhere in the US."""
    first = (started_at + min(_US_UTC_OFFSETS)).date()
    last = (ended_at + max(_US_UTC_OFFSETS)).date()
    days = set()
    day = first
    while day <= last:
        days.add(day)
        day += timedelta(days=1)
    return frozenset(days)


def mentions_weekday(text: str, agreed: date, call_days: Collection[date]) -> bool:
    """True if ``text`` (lowercased) names the agreed date by its weekday ("Thursday", "this
    Thursday"), and that weekday means the agreed date whichever of ``call_days`` the call was
    on: 1 to 6 days later. So "Thursday" said on Tue 17 Nov 2026 means 19 Nov.

    Never counts: no ``call_days``; the call day's own weekday (today or a week from now?);
    "next Thursday", "last Thursday" or "Thursday next week" (people disagree on which week);
    and a clause that also names a month and day, which ``mentions_date`` decides alone.
    """
    if not call_days:
        return False
    if not all(1 <= (agreed - day).days <= _MAX_WEEKDAY_AHEAD for day in call_days):
        return False
    weekday = _WEEKDAYS[agreed.weekday()]
    if not re.search(rf"\b{weekday}\b", text):
        return False
    if re.search(rf"\b(?:next|last|following)\s+{weekday}\b|\b(?:next|last) week\b", text):
        return False
    return not _names_a_month_day(text)


def _names_a_month_day(text: str) -> bool:
    """True if ``text`` (lowercased) names any month and day, such as "november 26"."""
    months = "|".join(rf"{m}|{m[:3]}\.?" for m in _MONTHS)
    day = r"\d{1,2}(?:st|nd|rd|th)?"
    return (
        re.search(rf"\b(?:{months})\s+(?:the\s+)?{day}(?!\d)", text) is not None
        or re.search(rf"(?<!\d){day}\s+(?:of\s+)?(?:{months})\b", text) is not None
    )


def spoken_fields(snapshot: dict[str, str], agent_lines: list[str]) -> list[str]:
    """Identifying profile fields whose value the agent said out loud (PO-3), sorted.

    ``snapshot`` maps field key to value (private: never log it). Only keys in
    ``SPOKEN_FIELD_KEYS`` are checked, each in the way it is said:

    - a date of birth as written, or as "April 12, 1990", "12 April 1990" or "04/12/1990"
    - an address by its street part, with "St" and "Street" (and similar) treated alike
    - a phone or member number by its digits, one spoken number at a time
    - a name as whole words; a name that is also an ordinary word ("May") only after a cue
      such as "on behalf of"
    - anything else as whole words, ignoring case

    Values shorter than two characters are skipped.
    """
    text = " ".join(agent_lines)
    spoken = []
    for key, value in snapshot.items():
        value = value.strip()
        if key not in SPOKEN_FIELD_KEYS or len(value) < 2:
            continue
        if _value_spoken(key, value, text):
            spoken.append(key)
    return sorted(spoken)


def _value_spoken(key: str, value: str, text: str) -> bool:
    folded = _fold(text)
    if key == "date_of_birth":
        return _date_spoken(value, text)
    if key == "address":
        return _address_spoken(value, folded)
    if key in ("calling_name", "full_name"):
        return _name_spoken(value, folded)
    value_digits = re.sub(r"\D", "", value)
    if key in ("phone", "insurance_member_id") and len(value_digits) >= 7:
        said_numbers = [re.sub(r"\D", "", s) for s in re.findall(r"\+?\d[\d\s().-]*\d", text)]
        national = value_digits[-10:]
        return any(national in said for said in said_numbers)
    return _contains_token(folded, _fold(value))


def _date_spoken(value: str, text: str) -> bool:
    day = valid_iso_date(value)
    if day is None:
        return _contains_token(_fold(text), _fold(value))
    folded = _fold(text)
    year = rf",?\s+{day.year}\b"  # right after the day, not anywhere in the call
    if value in text or any(re.search(p + year, folded) for p in _date_patterns(day)):
        return True
    numeric = rf"(?<!\d)0?{day.month}[/.-]0?{day.day}[/.-](?:{day.year}|{day.year % 100:02d})(?!\d)"
    return re.search(numeric, text) is not None


def _street_tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", _fold(text))
    return [_STREET_WORDS.get(word, word) for word in words]


def _address_spoken(value: str, folded: str) -> bool:
    street = value.split(",")[0]
    wanted = _street_tokens(street)
    if len(wanted) < 2:
        return False
    said = _street_tokens(folded)
    width = len(wanted)
    return any(said[i : i + width] == wanted for i in range(len(said) - width + 1))


def _name_spoken(value: str, folded: str) -> bool:
    name = _fold(value)
    if " " not in name and name in AMBIGUOUS_NAMES:
        # "a table for May 24" is a date, not the name: skip a match followed by a day.
        pattern = rf"\b{NAME_CUES}\s+{re.escape(name)}\b(?!\s*\d)"
        return re.search(pattern, folded) is not None
    return _contains_token(folded, name)


def _contains_token(text: str, phrase: str) -> bool:
    """``phrase`` appears in ``text`` (both lowercased) and is not part of a longer word or
    number, so "Al" does not match "Alex"."""
    phrase = phrase.strip().casefold()
    if not phrase:
        return False
    pattern = rf"(?<![\w]){re.escape(phrase)}(?![\w])"
    return re.search(pattern, text.casefold()) is not None
