"""Reads the structured ``rules`` out of a brief's typed items (PR 4 of the structured-offers plan).

Pure: no database, no network, no model. A brief item is the user's own words ("before 3pm",
"11 am pickup too"). Python can only compare an offer with it when it has ``rules``
(``contracts/brief-rule.v1.json``, scored by ``app/calls/scoring.py``). Without them the gates
step aside and the model alone decides, and a call accepted 12 PM against "11am is fine".

What it reads, and only this:

- **Times.** "before 3pm" (latest 14:59), "by 10am" and "no later than 10am" (latest 10:00),
  "after 6pm" (earliest 18:01), "from 6pm" and "6pm or later" (earliest 18:00), "between 6:30pm and
  8pm", "noon", "morning" (latest 11:59), "afternoon" (12:00 to 17:59). A time needs an am/pm,
  "noon" or a 24-hour form ("19:30"): a bare "3" or "7:30" could be either half of the day.
- **Days.** An explicit month and day (``requested_day.parse_requested_day``), "today",
  "tomorrow" and a bare weekday ("Friday", the next one). Never "next Friday".
- **Party size.** "party of 6", "table for 4", "6 people" (exactly), "at least 4" or "4 or more"
  (min), "up to 6" and "no more than 6" (max).
- **A person.** "with Dr. Rivera", with a title.

An item with nothing it can read stays text only. A brief is never blocked by a rule it can't
parse. When it *almost* can, such as a lone time ("11 am pickup too": the latest, or the only
time that works?), it says so in a ``Clarification`` instead of guessing: the chat should ask.
The one safe reading is a lone time in a fallback that is later than a deadline the brief
already has: that can only relax the deadline, so it is read as a later latest.

A deadline in the goal ("need to pick up by tomorrow 10am") becomes a constraint of its own,
in the user's words, when no constraint already says it: it is the user's own requirement,
and without it nothing could check an offer against it. "By tomorrow 10am" is read as
tomorrow, at or before 10:00 (a day rule plus a time rule; a datetime would need a new rule).

Dates are resolved against ``today`` when the brief is saved. A brief started a day later
keeps the dates it was saved with.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.calls.requested_day import parse_requested_day

Rule = dict[str, Any]

LIST_KEYS = ("constraints", "preferences", "fallbacks")
# The most items the chat keeps in one list (frontend chat.ts LIST_MAX_ITEMS).
LIST_MAX_ITEMS = 5

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

_MERIDIEM = r"(?:a\.?m\.?|p\.?m\.?)"
# One time of day: "10am", "10 am", "10:30 pm", "noon", "midnight", or 24-hour "19:30".
_TIME = (
    rf"(?:\bnoon\b|\bmidnight\b|"
    rf"(?P<hour>\d{{1,2}})(?::(?P<minute>\d{{2}}))?\s*(?P<meridiem>{_MERIDIEM})?)"
)
_TIME_RE = re.compile(rf"(?<![\d:/.]){_TIME}(?![\w/]|\.\d)", re.IGNORECASE)


def _clock_pattern(prefix: str) -> str:
    """A time with named groups ``<prefix>h``, ``<prefix>m`` and ``<prefix>p`` (am/pm)."""
    return (
        rf"(?P<{prefix}h>\d{{1,2}})(?::(?P<{prefix}m>\d{{2}}))?\s*"
        rf"(?P<{prefix}p>{_MERIDIEM})?"
    )


# What comes before a time, nearest first. "before 3pm" excludes 3pm itself (latest 14:59).
_LATEST_EXCLUSIVE = ("before", "prior to", "earlier than")
_LATEST_INCLUSIVE = (
    "by",
    "no later than",
    "not later than",
    "until",
    "till",
    "up to",
    "not after",
    "at or before",
    "latest",
)
_EARLIEST_EXCLUSIVE = ("after", "later than")
_EARLIEST_INCLUSIVE = (
    "from",
    "starting",
    "no earlier than",
    "not before",
    "at or after",
    "earliest",
)
_BEFORE_TIME = re.compile(
    r"\b(?P<word>at or before|at or after|no later than|not later than|no earlier than|"
    r"prior to|earlier than|later than|not before|not after|starting|latest|earliest|"
    r"before|after|until|till|from|up to|by)\s+(?:\w+\s+){0,2}$",
    re.IGNORECASE,
)
_AFTER_TIME = re.compile(
    r"^\s*(?:or|and)\s+(?P<word>later|earlier|onwards?|after)\b", re.IGNORECASE
)
_BETWEEN = re.compile(
    rf"\b(?:between|from)\s+{_clock_pattern('a')}\s*(?:and|to|-|–|until|till)\s*"
    rf"{_clock_pattern('b')}(?![\w/])",
    re.IGNORECASE,
)
# "6-8pm": no "between", so the end must say am or pm.
_RANGE = re.compile(
    rf"(?<![\d:/.]){_clock_pattern('a')}\s*(?:-|–|to)\s*{_clock_pattern('b')}(?![\w/])",
    re.IGNORECASE,
)

_PARTY_EXACT = (
    re.compile(r"\bparty of (\d{1,2})\b", re.IGNORECASE),
    re.compile(r"\b(?:table|reservation|booking) for (\d{1,2})\b(?!\s*(?:pm|am|:))", re.IGNORECASE),
    re.compile(r"\bgroup of (\d{1,2})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s+(?:people|persons|guests|pax|diners)\b", re.IGNORECASE),
    re.compile(r"\bfor (\d{1,2}) (?:of us|people|persons|guests)\b", re.IGNORECASE),
)
_PARTY_MIN = (
    re.compile(r"\bat least (\d{1,2})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s*(?:or more|\+)(?:\s+(?:people|persons|guests))?", re.IGNORECASE),
)
_PARTY_MAX = (
    re.compile(r"\b(?:up to|at most|no more than|max(?:imum)?(?: of)?) (\d{1,2})\b", re.IGNORECASE),
)
_PERSON = re.compile(
    r"\b[Ww]ith\s+((?:Dr|Doctor|Mr|Mrs|Ms|Miss|Mx|Prof|Professor|Nurse)\.?\s+"
    r"[A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+)?)",
)


@dataclass(frozen=True)
class Answer:
    """One tap-able answer to a question. It settles the item with ``rules``, or rewrites its
    ``text`` (then the item is read again when the brief is next saved)."""

    label: str
    rules: list[Rule] | None = None
    text: str | None = None


@dataclass(frozen=True)
class Clarification:
    """Something the parser nearly read: the question to ask the user, and what each answer means.

    ``list`` is which list the item is in (``constraints``, ``preferences`` or ``fallbacks``),
    ``item`` its text as typed, so the chat knows what to change. There is no "skip" answer
    here: the chat adds it, and it means "decide on the call" (``rules: []`` on the item).
    """

    item: str
    list: str
    question: str
    answers: tuple[Answer, ...] = ()

    def to_json(self) -> dict[str, Any]:
        """The shape the API sends and the chat reads."""
        return {
            "item": self.item,
            "list": self.list,
            "question": self.question,
            "answers": [{"label": a.label, "rules": a.rules, "text": a.text} for a in self.answers],
        }


@dataclass(frozen=True)
class ParsedBrief:
    """A brief with its rules attached, and what is still unclear."""

    brief: dict[str, Any]
    clarifications: list[Clarification] = field(default_factory=list)


@dataclass(frozen=True)
class _Time:
    minutes: int  # minutes after midnight
    start: int  # where it starts in the text
    end: int


def _minutes(hour: int, minute: int, meridiem: str | None) -> int | None:
    """Minutes after midnight, or None if the time is ambiguous or not a time."""
    if minute > 59:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        return (hour % 12 + (12 if meridiem.lower().startswith("p") else 0)) * 60 + minute
    if 13 <= hour <= 23 or hour == 0:
        return hour * 60 + minute  # 24-hour form: "19:30", "00:15"
    return None  # "3" or "7:30": either half of the day


def _hhmm(minutes: int) -> str:
    minutes = max(0, min(minutes, 23 * 60 + 59))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _time_mentions(text: str) -> tuple[list[_Time], list[str]]:
    """Every clear time in ``text``, and the text of each time it could not read."""
    found: list[_Time] = []
    unreadable: list[str] = []
    for match in _TIME_RE.finditer(text):
        word = match.group(0).strip().lower()
        if word == "noon":
            found.append(_Time(12 * 60, match.start(), match.end()))
            continue
        if word == "midnight":
            found.append(_Time(0, match.start(), match.end()))
            continue
        hour, minute, meridiem = match["hour"], match["minute"], match["meridiem"]
        if meridiem is None and minute is None:
            # A bare number ("for 6", "11") is not a time unless something says so.
            if re.search(r"\b(?:at|by|before|after|until|till|from)\s*$", text[: match.start()]):
                unreadable.append(match.group(0).strip())
            continue
        minutes = _minutes(int(hour), int(minute or 0), meridiem)
        if minutes is None:
            unreadable.append(match.group(0).strip())
            continue
        found.append(_Time(minutes, match.start(), match.end()))
    return found, unreadable


def _day_rule(text: str, today: date) -> Rule | None:
    """A day rule from an explicit date, "today", "tomorrow" or a bare weekday, else None."""
    explicit = parse_requested_day(text, today)
    if explicit is not None:
        return {"kind": "date", "on": explicit.isoformat()}
    lowered = text.casefold()
    words = {"today": 0, "tomorrow": 1}
    named = [offset for word, offset in words.items() if re.search(rf"\b{word}\b", lowered)]
    if len(named) == 1:
        return {"kind": "date", "on": (today + timedelta(days=named[0])).isoformat()}
    if named:
        return None  # "today or tomorrow"
    if re.search(r"\b(?:next|last|this coming)\s+(?:" + "|".join(_WEEKDAYS) + r")\b", lowered):
        return None
    days = [d for d in _WEEKDAYS if re.search(rf"\b{d}\b", lowered)]
    if len(days) == 1:
        ahead = (_WEEKDAYS.index(days[0]) - today.weekday()) % 7 or 7
        return {"kind": "date", "on": (today + timedelta(days=ahead)).isoformat()}
    return None


def _party_rule(text: str) -> Rule | None:
    exact = {int(m[1]) for p in _PARTY_EXACT for m in p.finditer(text)}
    low = {int(m[1]) for p in _PARTY_MIN for m in p.finditer(text)}
    high = {int(m[1]) for p in _PARTY_MAX for m in p.finditer(text)}
    if len(exact) > 1 or len(low) > 1 or len(high) > 1:
        return None
    rule: Rule = {"kind": "party_size"}
    if exact and not (low or high):
        rule["exactly"] = exact.pop()
    elif low or high:
        if low:
            rule["min"] = low.pop()
        if high:
            rule["max"] = high.pop()
    else:
        return None
    values = [v for k, v in rule.items() if k != "kind"]
    return rule if all(1 <= v <= 50 for v in values) else None


def _person_rule(text: str) -> Rule | None:
    names = {" ".join(m[1].split()) for m in _PERSON.finditer(text)}
    if len(names) != 1:
        return None
    name = names.pop()
    return {"kind": "person", "name": name} if len(name) <= 60 else None


def _between(text: str) -> tuple[Rule | None, tuple[int, int] | None, str | None]:
    """A "between 6:30pm and 8pm" window: the time rule, the span of text it used, and the text
    of a window it could not read because no am/pm said which half of the day (for the chat to
    ask about)."""
    unreadable: str | None = None
    for pattern in (_BETWEEN, _RANGE):
        match = pattern.search(text)
        if match is None:
            continue
        ap, bp = match["ap"], match["bp"]
        if pattern is _RANGE and not bp:
            continue
        first = _minutes(int(match["ah"]), int(match["am"] or 0), ap or bp)
        second = _minutes(int(match["bh"]), int(match["bm"] or 0), bp or ap)
        if first is None or second is None:
            unreadable = unreadable or match.group(0).strip()
            continue
        if first > second and not ap and bp:
            first -= 12 * 60  # "between 11 and 1pm": 11am to 1pm
        if first > second or first < 0:
            continue
        rule: Rule = {"kind": "time", "earliest": _hhmm(first), "latest": _hhmm(second)}
        return rule, match.span(), None
    return None, None, unreadable


@dataclass(frozen=True)
class _Reading:
    rules: list[Rule]
    lone_times: list[str]  # clear times with no word that says earliest, latest or between
    unreadable: list[str]  # times that could be either half of the day
    latest: int | None  # the latest time it set, in minutes


def _read(text: str, today: date) -> _Reading:
    """Every rule in one typed item, plus what it could not settle."""
    rules: list[Rule] = []
    lone: list[str] = []
    day = _day_rule(text, today)
    if day:
        rules.append(day)
    time_rule: Rule = {"kind": "time"}
    latest_minutes: int | None = None
    window, used, vague_window = _between(text)
    if window:
        time_rule.update({k: v for k, v in window.items() if k != "kind"})
        latest_minutes = int(time_rule["latest"][:2]) * 60 + int(time_rule["latest"][3:])
    times, unreadable = _time_mentions(text)
    if vague_window:
        unreadable = [vague_window]  # "between 6 and 8": its numbers are not times on their own
    for mention in times:
        if used and used[0] <= mention.start < used[1]:
            continue
        before = _BEFORE_TIME.search(text[: mention.start])
        after = _AFTER_TIME.search(text[mention.end :])
        word = (before["word"] if before else "").casefold()
        tail = (after["word"] if after else "").casefold()
        label = text[mention.start : mention.end].strip()
        if word in _LATEST_EXCLUSIVE:
            time_rule["latest"] = _hhmm(mention.minutes - 1)
        elif word in _LATEST_INCLUSIVE or tail == "earlier":
            time_rule["latest"] = _hhmm(mention.minutes)
        elif word in _EARLIEST_EXCLUSIVE:
            time_rule["earliest"] = _hhmm(mention.minutes + 1)
        elif word in _EARLIEST_INCLUSIVE or tail in ("later", "onward", "onwards"):
            time_rule["earliest"] = _hhmm(mention.minutes)
        else:
            lone.append(label)
            continue
        if "latest" in time_rule:
            latest_minutes = int(time_rule["latest"][:2]) * 60 + int(time_rule["latest"][3:])
    if not used and not any(k in time_rule for k in ("earliest", "latest")) and not lone:
        part = _day_part(text)
        if part:
            time_rule.update(part)
    if "earliest" in time_rule or "latest" in time_rule:
        if (
            "earliest" in time_rule
            and "latest" in time_rule
            and time_rule["earliest"] > time_rule["latest"]
        ):
            return _Reading([], lone, unreadable, None)  # "after 8pm before 6pm": not a window
        rules.append(time_rule)
    for rule in (_party_rule(text), _person_rule(text)):
        if rule:
            rules.append(rule)
    return _Reading(rules, lone, unreadable, latest_minutes)


def _day_part(text: str) -> Rule | None:
    lowered = text.casefold()
    morning = re.search(r"\bmornings?\b", lowered) is not None
    afternoon = re.search(r"\bafternoons?\b", lowered) is not None
    if morning == afternoon:
        return None
    return {"latest": "11:59"} if morning else {"earliest": "12:00", "latest": "17:59"}


def _deadline_phrase(goal: str, today: date) -> tuple[str, list[Rule]] | None:
    """A deadline in the goal ("by tomorrow 10am"): the user's words for it, and its rules.

    Only a clear "by/before/no later than/until <time>", optionally with a day in between or
    around it, counts. The text is the goal's own words from the deadline word to the end of
    the time, so the review card shows what the user typed.
    """
    for mention in _time_mentions(goal)[0]:
        before = _BEFORE_TIME.search(goal[: mention.start])
        if before is None:
            continue
        word = before["word"].casefold()
        if word not in _LATEST_EXCLUSIVE and word not in _LATEST_INCLUSIVE:
            continue
        phrase = goal[before.start("word") : mention.end].strip()
        reading = _read(phrase, today)
        if not any(r["kind"] == "time" for r in reading.rules):
            continue
        if not any(r["kind"] == "date" for r in reading.rules):
            tail = re.split(r"[,.;!?]", goal[mention.end :], maxsplit=1)[0][:25]
            day = _day_rule(tail, today)
            if day:
                phrase = f"{phrase} {tail.strip()}"
                reading = _Reading([day, *reading.rules], [], [], reading.latest)
        return phrase[:1].upper() + phrase[1:], reading.rules
    return None


def _canonical(rules: Sequence[Rule]) -> list[list[tuple[str, Any]]]:
    return sorted(sorted(rule.items()) for rule in rules)


def _same_rules(left: Sequence[Rule], right: Sequence[Rule]) -> bool:
    return _canonical(left) == _canonical(right)


def _latest_bound(items: Sequence[Mapping[str, Any]]) -> int | None:
    """The deadline the items set, in minutes: the latest time of a rule that has no earliest.

    Only a pure deadline ("by 10am") counts. A window ("between 6pm and 8pm") has an earliest
    too, and a specific time beside it is more likely meant exactly than as a later latest.
    """
    bounds = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        for rule in item.get("rules") or []:
            if (
                rule.get("kind") == "time"
                and isinstance(rule.get("latest"), str)
                and "earliest" not in rule
            ):
                bounds.append(int(rule["latest"][:2]) * 60 + int(rule["latest"][3:]))
    return max(bounds) if bounds else None


def add_rules(brief: Mapping[str, Any], today: date) -> ParsedBrief:
    """The brief with ``rules`` on every item it can read, a deadline from the goal added as a
    constraint, and the questions it could not settle. Rules already on an item are kept, and
    saving the result again changes nothing.
    """
    out = dict(brief)
    clarifications: list[Clarification] = []
    # Constraints and preferences first, so a fallback can relax their deadline.
    constraints = [
        _with_rules(item, today, "constraints", clarifications)
        for item in out.get("constraints") or []
    ]
    implied = _deadline_phrase(str(out.get("goal") or ""), today)
    if implied is not None:
        text, rules = implied
        already = any(
            isinstance(i, Mapping)
            and (
                _same_rules(i.get("rules") or [], rules)
                or str(i.get("text", "")).casefold() == text.casefold()
            )
            for i in constraints
        )
        if not already and len(constraints) < LIST_MAX_ITEMS:
            constraints.append({"text": text, "rules": rules})
    out["constraints"] = constraints
    out["preferences"] = [
        _with_rules(item, today, "preferences", clarifications)
        for item in out.get("preferences") or []
    ]
    deadline = _latest_bound(constraints)
    out["fallbacks"] = [
        _with_rules(item, today, "fallbacks", clarifications, deadline)
        for item in out.get("fallbacks") or []
    ]
    return ParsedBrief(out, clarifications)


def _with_rules(
    item: Any,
    today: date,
    key: str,
    clarifications: list[Clarification],
    deadline: int | None = None,
) -> Any:
    if not isinstance(item, Mapping) or "rules" in item:
        return item  # has rules, or the user decided it has none (``rules: []``): keep it
    text = str(item.get("text", ""))
    reading = _read(text, today)
    rules = list(reading.rules)
    for label in reading.lone_times:
        minutes = _lone_minutes(label)
        if (
            minutes is not None
            and key == "fallbacks"
            and deadline is not None
            and minutes >= deadline
        ):
            rules = _with_latest(rules, minutes)  # a later latest: it can only relax the deadline
        elif minutes is not None:
            clarifications.append(_ask_about(text, key, minutes))
    for label in reading.unreadable:
        clarifications.append(_ask_am_pm(text, key, label))
    return {**item, "rules": rules} if rules else dict(item)


def _lone_minutes(label: str) -> int | None:
    found, _ = _time_mentions(label)
    return found[0].minutes if found else None


def _with_latest(rules: list[Rule], minutes: int) -> list[Rule]:
    rest = [r for r in rules if r["kind"] != "time"]
    return [*rest, {"kind": "time", "latest": _hhmm(minutes)}]


def _clock(minutes: int) -> str:
    hour, minute = divmod(minutes, 60)
    suffix = "am" if hour < 12 else "pm"
    return f"{hour % 12 or 12}{f':{minute:02d}' if minute else ''}{suffix}"


def _ask_about(text: str, key: str, minutes: int) -> Clarification:
    """A time with no "before", "after" or "between" next to it."""
    clock = _clock(minutes)
    return Clarification(
        item=text,
        list=key,
        question=(
            f'You wrote "{text}". Is {clock} the latest you would accept, '
            "or the only time that works?"
        ),
        answers=(
            Answer(f"{clock} or earlier", rules=[{"kind": "time", "latest": _hhmm(minutes)}]),
            Answer(f"{clock} or later", rules=[{"kind": "time", "earliest": _hhmm(minutes)}]),
            Answer(
                f"Only {clock}",
                rules=[{"kind": "time", "earliest": _hhmm(minutes), "latest": _hhmm(minutes)}],
            ),
        ),
    )


def _ask_am_pm(text: str, key: str, label: str) -> Clarification:
    """A time with no am or pm: each answer rewrites the item with it, and the item is then
    read again (a lone time may then be asked about next)."""
    at = text.find(label)
    stem, rest = (text[: at + len(label)], text[at + len(label) :]) if at >= 0 else (text, "")
    return Clarification(
        item=text,
        list=key,
        question=f'You wrote "{text}". Did you mean {label} in the morning or in the evening?',
        answers=(
            Answer("Morning (am)", text=f"{stem}am{rest}"),
            Answer("Afternoon or evening (pm)", text=f"{stem}pm{rest}"),
        ),
    )
