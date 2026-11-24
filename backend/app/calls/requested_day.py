"""The day the user asked for, read from their typed answer to "Which day?" (pure, no I/O).

The restaurant template keeps that answer as plain text in ``task_vars`` ("Dec 23"), with no year.
The agent used to get only those words, and a call reported its booking for 2026-10-23 when the
user had asked for December 23: the month was guessed. This reads the typed day into a real date
so the requirement list, not the model or the speech-to-text transcript, anchors the date.

It is deliberately strict: an explicit month and day ("Dec 23", "23rd of December", "12/23",
"Saturday 21 November", with an optional year) and nothing else. "Friday", "tomorrow" or two
different dates in one answer give None, and then nothing is anchored. The result is a ``date``
computed here, never the typed text, so it is safe to put in the prompt.
"""

import re
from collections.abc import Mapping
from datetime import date
from typing import Any

_MONTH_NAMES = (
    ("january", "jan"),
    ("february", "feb"),
    ("march", "mar"),
    ("april", "apr"),
    ("may", "may"),
    ("june", "jun"),
    ("july", "jul"),
    ("august", "aug"),
    ("september", "sept", "sep"),
    ("october", "oct"),
    ("november", "nov"),
    ("december", "dec"),
)
_MONTH_NUMBER = {name: number for number, names in enumerate(_MONTH_NAMES, 1) for name in names}
_MONTH = "(" + "|".join(sorted(_MONTH_NUMBER, key=len, reverse=True)) + r")\.?"
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?:,?\s+(\d{4}))?"

_MONTH_FIRST = re.compile(rf"\b{_MONTH}\s+(?:the\s+)?{_DAY}\b{_YEAR}", re.IGNORECASE)
_DAY_FIRST = re.compile(rf"\b{_DAY}\s+(?:of\s+)?{_MONTH}\b{_YEAR}", re.IGNORECASE)
_NUMERIC = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?(?![\d/])")

# The brief variables that hold the day the user asked for (the restaurant template's "day").
DAY_VARIABLES = ("day", "date")


def parse_requested_day(text: str, today: date) -> date | None:
    """The date ``text`` names, or None if it names none or more than one.

    Without a year, the next such day on or after ``today`` ("Dec 23" typed in November is
    this December; typed on 24 December it is next year's).
    """
    found: set[tuple[int, int, int | None]] = set()
    for match in _MONTH_FIRST.finditer(text):
        found.add((_MONTH_NUMBER[match[1].casefold()], int(match[2]), _year(match[3])))
    for match in _DAY_FIRST.finditer(text):
        found.add((_MONTH_NUMBER[match[2].casefold()], int(match[1]), _year(match[3])))
    for match in _NUMERIC.finditer(text):
        found.add((int(match[1]), int(match[2]), _year(match[3])))
    days = {_resolve(month, day, year, today) for month, day, year in found}
    return days.pop() if len(days) == 1 else None


def _year(value: str | None) -> int | None:
    if value is None:
        return None
    return 2000 + int(value) if len(value) == 2 else int(value)  # noqa: PLR2004 - "26" = 2026


def _resolve(month: int, day: int, year: int | None, today: date) -> date | None:
    try:
        if year is not None:
            return date(year, month, day)
        candidate = date(today.year, month, day)
        return candidate if candidate >= today else date(today.year + 1, month, day)
    except ValueError:  # not a real day, such as 30 February
        return None


def requested_day(brief: Mapping[str, Any] | None, today: date) -> date | None:
    """The day the user asked for in ``brief`` (brief.v1 JSON), or None if it can't be read.

    Only a literal the user typed counts; a profile field reference never holds a day.
    """
    task_vars = brief.get("task_vars") if brief else None
    if not isinstance(task_vars, Mapping):
        return None
    for name in DAY_VARIABLES:
        spec = task_vars.get(name)
        literal = spec.get("literal") if isinstance(spec, Mapping) else None
        if isinstance(literal, str):
            return parse_requested_day(literal, today)
    return None


# A range rule ("between Nov 20 and Dec 5") is expanded into days only up to this long.
_MAX_RANGE_DAYS = 62


def rule_days(brief: Mapping[str, Any] | None) -> set[date]:
    """Every day a date rule in the brief allows: ``on``, or each day of ``earliest``-``latest``.

    Reads constraints, preferences and fallbacks (brief.v1 JSON). A value that isn't a real
    YYYY-MM-DD date, or a range longer than ``_MAX_RANGE_DAYS``, is skipped.
    """
    days: set[date] = set()
    if not brief:
        return days
    for section in ("constraints", "preferences", "fallbacks"):
        items = brief.get(section)
        for item in items if isinstance(items, list) else []:
            rules = item.get("rules") if isinstance(item, Mapping) else None
            for rule in rules if isinstance(rules, list) else []:
                if isinstance(rule, Mapping) and rule.get("kind") == "date":
                    days |= _rule_range(rule)
    return days


def _iso(value: object) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _rule_range(rule: Mapping[str, Any]) -> set[date]:
    exact = _iso(rule.get("on"))
    first, last = _iso(rule.get("earliest")), _iso(rule.get("latest"))
    days = {exact} if exact is not None else set()
    if first is not None and last is not None and 0 <= (last - first).days <= _MAX_RANGE_DAYS:
        days |= {date.fromordinal(n) for n in range(first.toordinal(), last.toordinal() + 1)}
    else:
        days |= {d for d in (first, last) if d is not None}
    return days


def business_named_day(said: str, day: date) -> bool:
    """True if ``said`` (the business's words, lowercased) names ``day`` in any loose way: its
    month and day, its weekday, or its day of the month as an ordinal ("the 24th").

    Deliberately generous: it only decides whether the business could have agreed to this
    day, to avoid refusing an agent that honestly reports the day the business offered.
    """
    month = next(names for number, names in enumerate(_MONTH_NAMES, 1) if number == day.month)
    months = "|".join(month)
    ordinal = rf"{day.day}(?:st|nd|rd|th)"
    patterns = (
        rf"\b(?:{months})\.?\s+(?:the\s+)?{day.day}(?:st|nd|rd|th)?\b",
        rf"\b{day.day}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{months})\b",
        rf"\b{ordinal}\b",
        rf"\b{day:%A}\b".lower(),
    )
    return any(re.search(pattern, said) for pattern in patterns)
