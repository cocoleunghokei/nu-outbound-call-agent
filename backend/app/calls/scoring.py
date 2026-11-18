"""Scores the offer the agent heard against the brief's structured rules (DC-4). Pure code.

Plan: ``docs/superpowers/plans/2026-11-18-structured-offers.md``, section 3. The in-call
model writes down what the business offered (``offer`` on ``ask_user`` and
``report_decision``: date, time, party size, person). This module compares that with the
``rules`` the brief stores on its constraints, preferences and fallbacks
(``contracts/brief-rule.v1.json``), so a comparison such as "is 3pm before 3pm?" is never left
to the model.

Every brief item gets one of three results:

- ``pass``: it has rules, and the offer satisfies every one of them.
- ``fail``: at least one of its rules is broken by a value the offer states.
- ``unknown``: it has no rules (text only), a rule has no bound, or the offer doesn't say
  the value a rule needs. Python has no opinion, and the model's own judgment stands.

No database, no network, no clock: the offer's date is already resolved by the model, and
the brief's dates are absolute. Malformed values count as unknown rather than raising,
because a scoring bug must never block a call.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

Check = Literal["pass", "fail", "unknown"]

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# Words that say who someone is, not which person: "Dr. Rivera" and "Rivera" are the same.
_TITLES = frozenset({"dr", "doctor", "mr", "mrs", "ms", "miss", "mx", "prof", "professor"})


@dataclass(frozen=True)
class ItemResult:
    """One constraint, preference or fallback, with its result against the offer."""

    text: str
    check: Check


@dataclass(frozen=True)
class OfferScore:
    """Every structured brief item checked against one offer."""

    constraints: tuple[ItemResult, ...]
    preferences: tuple[ItemResult, ...]
    fallbacks: tuple[ItemResult, ...]

    @property
    def fits(self) -> bool | None:
        """False if a constraint fails; True if every constraint passes; else None (unknown).

        A brief with no constraints at all fits (Q8: nothing typed is broken).
        """
        checks = [c.check for c in self.constraints]
        if "fail" in checks:
            return False
        if all(c == "pass" for c in checks):
            return True
        return None

    @property
    def matches_fallback(self) -> bool | None:
        """True if some fallback passes; False if every fallback fails (or there are none);
        else None, because an unknown fallback might still match."""
        checks = [f.check for f in self.fallbacks]
        if "pass" in checks:
            return True
        if all(c == "fail" for c in checks):
            return False
        return None

    @property
    def broken_constraint(self) -> str | None:
        """The text of the first constraint the offer breaks, if any."""
        return next((c.text for c in self.constraints if c.check == "fail"), None)

    @property
    def missed_preferences(self) -> tuple[str, ...]:
        """The texts of the structured preferences the offer misses."""
        return tuple(p.text for p in self.preferences if p.check == "fail")


def _text(value: object, pattern: re.Pattern[str]) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _within(value: str | int, low: str | int | None, high: str | int | None) -> Check:
    """pass/fail for ``low <= value <= high`` (either bound optional); unknown if neither."""
    if low is None and high is None:
        return "unknown"
    if low is not None and value < low:  # type: ignore[operator]
        return "fail"
    if high is not None and value > high:  # type: ignore[operator]
        return "fail"
    return "pass"


def _name_tokens(name: str) -> frozenset[str]:
    words = re.findall(r"[^\W\d_]+", name.casefold())
    return frozenset(w for w in words if w not in _TITLES)


def same_person(wanted: str, offered: str) -> bool:
    """True if two names refer to the same person: "Dr. Rivera" matches "Rivera" and "Dr. Maria
    Rivera", but not "Dr. Patel". One name's words must all appear in the other's."""
    a, b = _name_tokens(wanted), _name_tokens(offered)
    return bool(a) and bool(b) and (a <= b or b <= a)


def check_rule(rule: Mapping[str, Any], offer: Mapping[str, Any]) -> Check:
    """One rule against the offer. Unknown when the offer doesn't state the value it needs."""
    kind = rule.get("kind")
    if kind == "date":
        day = _text(offer.get("date"), _DATE)
        if day is None:
            return "unknown"
        on = _text(rule.get("on"), _DATE)
        if on is not None:
            return "pass" if day == on else "fail"
        return _within(day, _text(rule.get("earliest"), _DATE), _text(rule.get("latest"), _DATE))
    if kind == "time":
        time = _text(offer.get("time"), _TIME)
        if time is None:
            return "unknown"
        return _within(time, _text(rule.get("earliest"), _TIME), _text(rule.get("latest"), _TIME))
    if kind == "party_size":
        size = _count(offer.get("party_size"))
        if size is None:
            return "unknown"
        exactly = _count(rule.get("exactly"))
        if exactly is not None:
            return "pass" if size == exactly else "fail"
        return _within(size, _count(rule.get("min")), _count(rule.get("max")))
    if kind == "person":
        offered, wanted = offer.get("person"), rule.get("name")
        if not isinstance(offered, str) or not isinstance(wanted, str):
            return "unknown"
        if not _name_tokens(offered) or not _name_tokens(wanted):
            return "unknown"
        return "pass" if same_person(wanted, offered) else "fail"
    return "unknown"  # a kind this version doesn't know


def check_item(item: Mapping[str, Any], offer: Mapping[str, Any]) -> Check:
    """A brief item: fail if any rule fails, pass if all pass, else unknown (no rules too)."""
    rules = item.get("rules")
    if not isinstance(rules, list) or not rules:
        return "unknown"
    checks = [check_rule(r, offer) if isinstance(r, Mapping) else "unknown" for r in rules]
    if "fail" in checks:
        return "fail"
    if all(c == "pass" for c in checks):
        return "pass"
    return "unknown"


def _items(brief: Mapping[str, Any], key: str, offer: Mapping[str, Any]) -> tuple[ItemResult, ...]:
    raw = brief.get(key)
    items: Sequence[Any] = raw if isinstance(raw, list) else []
    return tuple(
        ItemResult(text=str(item.get("text", "")), check=check_item(item, offer))
        for item in items
        if isinstance(item, Mapping)
    )


def score_offer(brief: Mapping[str, Any], offer: Mapping[str, Any]) -> OfferScore:
    """Check every constraint, preference and fallback of ``brief`` (brief.v1 JSON)."""
    return OfferScore(
        constraints=_items(brief, "constraints", offer),
        preferences=_items(brief, "preferences", offer),
        fallbacks=_items(brief, "fallbacks", offer),
    )


def score_tool_call(
    brief: Mapping[str, Any] | None, arguments: Mapping[str, Any]
) -> OfferScore | None:
    """The score for a tool call's ``offer``, or None when there is no brief or no offer."""
    offer = arguments.get("offer")
    if brief is None or not isinstance(offer, Mapping) or not offer:
        return None
    return score_offer(brief, offer)
