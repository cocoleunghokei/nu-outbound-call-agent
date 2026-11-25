"""Result items for the result page (PO-2): what the user asked for next to what they got.

The agent writes ``result_items`` in ``report_outcome``
(``contracts/tools/report_outcome.args.v1.json``).
That is a claim, so before the page shows it this module:

- fills ``asked`` from the stored brief, never from the agent (``ref`` says which item),
- finds the agent's ``business_said`` quote in the business's side of the transcript and turns it
  into ``evidence`` (the ``seq`` of each line it came from),
- checks that a ``cost_cents`` amount was really said by the business,
- turns an ``as_asked`` row into ``changed`` when the agreed date or time breaks the brief
  item's rules,
- downgrades ``approved_by: user`` to ``none`` when the user never replied during the call.

A row with no evidence (or an unsaid amount) is *unverified*. Unverified ``as_asked`` rows are
shown, marked ``verified: false``; every other unverified row is left out, because a false claim of
a change, a fee or a waived cost costs the user more. Design: ``docs/call-result-items.md``.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from app.contracts.generated import OutcomeDetails, ReportOutcomeArgs, ResultItem, ResultItemRef

# (seq, role, text) of one transcript line, in spoken order.
SeqTurn = tuple[int, str, str]

# A quote this short ("yes") matches almost any business line, so it proves nothing.
MIN_QUOTE_WORDS = 2
# Words of the business's own that a quote may skip between two of its words (a stumble or filler).
MAX_SKIPPED_WORDS = 3

_AS_ASKED = "as_asked"
_CHANGED = "changed"
_HH_MM = re.compile(r"^\d{2}:\d{2}$")
_NUMBER = re.compile(r"\d+(?:\.\d{1,2})?")

_ONES = {
    word: value
    for value, word in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen".split()
    )
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}


class ResultItemOut(BaseModel):
    """One asked-vs-got row as the result page gets it."""

    label: str
    #: The user's own words from the brief, or None (an ``extra`` row, or an item the brief lacks).
    asked: str | None
    got: str
    status: str
    reason: str | None
    approved_by: str | None
    #: Whole cents for a fee or price, only when the business said that amount.
    cost_cents: int | None
    #: ``seq`` of the transcript lines the agent's ``business_said`` quote was found in.
    evidence: list[int]
    #: False when no transcript line backs the row. Only ``as_asked`` rows are shown unverified.
    verified: bool


def _fold(text: str) -> str:
    """Casefolded words only: punctuation and repeated spaces don't matter when matching."""
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def _business_runs(turns: Sequence[SeqTurn]) -> list[list[tuple[str, int]]]:
    """Runs of consecutive business lines as (word, seq) pairs, one pair per word.

    The speech-to-text service splits one sentence over several lines ("We have" / "all of those"
    / "except mochi"), so a quote has to be searched across the lines of one run. It is never
    searched across an agent line: that would join two separate things the business said.
    """
    runs: list[list[tuple[str, int]]] = []
    current: list[tuple[str, int]] = []
    for seq, role, text in turns:
        if role == "business":
            current.extend((word, seq) for word in _fold(text).split())
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def find_evidence(quote: str | None, turns: Sequence[SeqTurn]) -> list[int]:
    """The ``seq`` of every business line a quote spans, or [] if the business never said it.

    Every word of the quote must appear, in order, within one run of business lines, with at
    most ``MAX_SKIPPED_WORDS`` other words between two of them. That lets a quote leave out a
    stumble ("Is there... is marshmallow okay?" quoted as "is marshmallow okay") but not
    reorder, add or invent words.
    """
    words = _fold(quote or "").split()
    if len(words) < MIN_QUOTE_WORDS:
        return []
    for run in _business_runs(turns):
        for start, (first, _) in enumerate(run):
            if first != words[0]:
                continue
            matched = _match_from(words, run, start)
            if matched is not None:
                return matched
    return []


def _match_from(words: list[str], run: list[tuple[str, int]], start: int) -> list[int] | None:
    """The lines of the quote's words matched in order from ``start``, or None."""
    seqs = [run[start][1]]
    at = start
    for word in words[1:]:
        window = run[at + 1 : at + 2 + MAX_SKIPPED_WORDS]
        offset = next((i for i, (w, _) in enumerate(window) if w == word), None)
        if offset is None:
            return None
        at += 1 + offset
        seqs.append(run[at][1])
    return sorted(set(seqs))


def _numbers_said(text: str) -> set[float]:
    """Amounts in the business's words, digits ("$3", "3.50") and words ("three dollars")."""
    found = {float(match) for match in _NUMBER.findall(text)}
    words = _fold(text).split()
    for i, word in enumerate(words):
        if word in _TENS:
            nxt = words[i + 1] if i + 1 < len(words) else ""
            found.add(float(_TENS[word] + (_ONES[nxt] if 1 <= _ONES.get(nxt, 0) <= 9 else 0)))
        elif word in _ONES:
            found.add(float(_ONES[word]))
    return found


def amount_said(cents: int, turns: Sequence[SeqTurn]) -> bool:
    """True if a business line says this amount (3 dollars for 300 cents)."""
    dollars = cents / 100
    said = " ".join(text for _, role, text in turns if role == "business")
    return any(abs(number - dollars) < 0.005 for number in _numbers_said(said))


def _brief_item(brief: Mapping[str, Any] | None, ref: ResultItemRef) -> Mapping[str, Any] | None:
    """The brief's constraint, preference or fallback a ref points to, or None."""
    if (
        brief is None
        or ref.index is None
        or ref.kind.value not in ("constraint", "preference", "fallback")
    ):
        return None
    items = brief.get(
        {"constraint": "constraints", "preference": "preferences", "fallback": "fallbacks"}[
            ref.kind.value
        ]
    )
    if not isinstance(items, list) or not 0 <= ref.index < len(items):
        return None
    item = items[ref.index]
    return item if isinstance(item, Mapping) else None


def asked_text(brief: Mapping[str, Any] | None, ref: ResultItemRef) -> str | None:
    """What the user asked for, in their own words, from the brief. None when it can't be told.

    A task variable that comes from the profile (``field_ref``) is never shown: it would be a
    profile value, and the brief holds keys only.
    """
    if brief is not None and ref.kind.value == "task_var" and ref.key:
        task_vars = brief.get("task_vars")
        value = task_vars.get(ref.key) if isinstance(task_vars, Mapping) else None
        literal = value.get("literal") if isinstance(value, Mapping) else None
        return literal.strip() or None if isinstance(literal, str) else None
    item = _brief_item(brief, ref)
    text = item.get("text") if item else None
    return text.strip() or None if isinstance(text, str) else None


def breaks_rules(
    brief: Mapping[str, Any] | None, ref: ResultItemRef, details: OutcomeDetails
) -> bool:
    """True if the agreed date or time breaks a typed rule (``brief-rule.v1``) of the brief item.

    Only the date and time rules can be compared with ``details``. A time that isn't ``HH:MM``
    can't be compared and counts as not broken.
    """
    item = _brief_item(brief, ref)
    rules = item.get("rules") if item else None
    if not isinstance(rules, list):
        return False
    for rule in rules:
        if not isinstance(rule, Mapping):
            continue
        if rule.get("kind") == "date" and details.date:
            value = details.date
            if rule.get("on") not in (None, value):
                return True
            if _before(value, rule.get("earliest")) or _after(value, rule.get("latest")):
                return True
        if rule.get("kind") == "time" and details.time and _HH_MM.match(details.time):
            value = details.time
            if _before(value, rule.get("earliest")) or _after(value, rule.get("latest")):
                return True
    return False


def _before(value: str, bound: object) -> bool:
    return isinstance(bound, str) and value < bound


def _after(value: str, bound: object) -> bool:
    return isinstance(bound, str) and value > bound


def shown_result_items(
    report: ReportOutcomeArgs | None,
    brief: Mapping[str, Any] | None,
    turns: Sequence[SeqTurn],
    details: OutcomeDetails,
    user_replied: bool,
) -> list[ResultItemOut]:
    """The rows the result page may show, in the agent's order.

    ``details`` are the already-checked details (``shown_details``), so a date or time the agent
    made up never decides a status. ``user_replied`` is True if the user answered a ping or sent
    an instruction during the call.
    """
    shown: list[ResultItemOut] = []
    for item in (report.result_items or []) if report else []:
        row = _checked(item, brief, turns, details, user_replied)
        if row is not None:
            shown.append(row)
    return shown


def _checked(
    item: ResultItem,
    brief: Mapping[str, Any] | None,
    turns: Sequence[SeqTurn],
    details: OutcomeDetails,
    user_replied: bool,
) -> ResultItemOut | None:
    evidence = find_evidence(item.business_said, turns)
    cost_ok = item.cost_cents is None or amount_said(item.cost_cents, turns)
    verified = bool(evidence) and cost_ok

    status = item.status.value
    if status == _AS_ASKED and breaks_rules(brief, item.ref, details):
        status = _CHANGED
    if not verified and status != _AS_ASKED:
        return None

    label, got = item.label.strip(), item.got.strip()
    if not label or not got:
        return None  # blank after trimming: nothing to show

    approved_by = item.approved_by.value if item.approved_by else None
    if approved_by == "user" and not user_replied:
        approved_by = "none"
    return ResultItemOut(
        label=label,
        asked=asked_text(brief, item.ref),
        got=got,
        status=status,
        reason=(item.reason or "").strip() or None,
        approved_by=approved_by,
        cost_cents=item.cost_cents if cost_ok else None,
        evidence=evidence,
        verified=verified,
    )
