"""Post-call eval checks, shared by the report (``run.py``) and the offline test
(``tests/test_postcall_evals.py``).

Each case in ``cases.json`` is one ended call. The checks follow the AI evaluation scope:

- item 10, outcome (PO-1): ``decide.decide_outcome`` must give ``expect.outcome``. A wrong
  "booked" (or "cancelled" / "rescheduled") is the worst error and is counted on its own.
- item 12, what was shared (PO-3): ``decide.spoken_fields`` must give exactly
  ``expect.spoken``. A missed field breaks the trust promise; an extra one alarms the user.
- item 11, summary (PO-2): ``decide.confirmed_details`` decides which agreed details the
  result page shows. The shown ones must match ``expect.summary`` (date, time,
  confirmation_number, staff_name), and none of ``must_not_mention`` may show. Open items are
  the agent's own words, so rules can't check them: ``open_items_any_of`` is reported as
  "in-call" and judged in the in-call eval (backend/evals/incall).
- item 13, the money guardrail: not built yet. Its expectations are reported as "not built"
  so they are ready when the code is.

A case with ``known_gap`` (the reason) records a right answer the code doesn't give yet. Its
outcome failure is reported as "known gap" instead of failing, and it is not counted as a
wrong "booked". The fix removes the flag in the same PR.

No database, no network and no paid API: these run in CI.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from app.contracts.generated import OutcomeDetails, ReportOutcomeArgs
from app.postcall.decide import (
    NEEDS_EVIDENCE,
    call_days_for,
    confirmed_details,
    decide_outcome,
    spoken_fields,
)

CASES_PATH = Path(__file__).with_name("cases.json")

Status = Literal["pass", "fail", "not_built", "in_call", "known_gap"]

CASE_KEYS = frozenset(
    {
        "id",
        "about",
        "called_at",
        "report",
        "ended_reason",
        "pause_capped",
        "transcript",
        "events",
        "expect",
        "known_gap",
    }
)
EXPECT_KEYS = frozenset({"outcome", "spoken", "summary", "guardrail_breach"})
ROLES = frozenset({"agent", "business"})
SUMMARY_FIELDS = ("date", "time", "confirmation_number", "staff_name")
SUMMARY_KEYS = frozenset({*SUMMARY_FIELDS, "must_not_mention", "open_items_any_of"})
# A must_not_mention phrase that names a field means that field must not show at all.
FIELD_PHRASES = {field.replace("_", " "): field for field in SUMMARY_FIELDS}


@dataclass(frozen=True)
class PostcallCase:
    """One ended call and its right answers."""

    id: str
    about: str
    report: ReportOutcomeArgs | None
    call_days: frozenset[date]
    ended_reason: str
    pause_capped: bool
    agent_lines: list[str]
    business_lines: list[str]
    events: list[dict[str, Any]]
    expect: dict[str, Any]
    known_gap: str | None = None


@dataclass(frozen=True)
class ItemResult:
    """The result of one evaluation item for one case."""

    item: str
    status: Status
    detail: str = ""


@dataclass
class CaseReport:
    case_id: str
    results: list[ItemResult] = field(default_factory=list)
    outcome_ms: float = 0.0
    # A wrong outcome that claims something happened (booked, cancelled or rescheduled).
    wrong_booked: bool = False
    # The same, in a known-gap case: reported apart so it stays visible.
    known_gap_booked: bool = False


def load_cases() -> tuple[dict[str, str], list[PostcallCase]]:
    """The fake profile snapshot (key -> value) and the cases from cases.json."""
    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    profile = {p["key"]: p["value"] for p in data["profile"]}
    cases = []
    for raw in data["cases"]:
        _check_keys(raw)
        transcript = raw.get("transcript", [])
        cases.append(
            PostcallCase(
                id=raw["id"],
                about=raw.get("about", ""),
                report=(
                    ReportOutcomeArgs.model_validate(raw["report"])
                    if raw.get("report") is not None
                    else None
                ),
                call_days=_call_days(raw.get("called_at")),
                ended_reason=raw.get("ended_reason", ""),
                pause_capped=bool(raw.get("pause_capped", False)),
                agent_lines=[text for role, text in transcript if role == "agent"],
                business_lines=[text for role, text in transcript if role == "business"],
                events=list(raw.get("events", [])),
                expect=dict(raw["expect"]),
                known_gap=raw.get("known_gap"),
            )
        )
    return profile, cases


def _call_days(called_at: str | None) -> frozenset[date]:
    """The possible local days of a case's call, from its ``called_at`` (ISO, with a zone)."""
    if called_at is None:
        return frozenset()
    moment = datetime.fromisoformat(called_at)
    if moment.tzinfo is None:
        raise ValueError(f"called_at {called_at!r} needs a time zone, such as Z")
    return call_days_for(moment, moment)


def _check_keys(raw: dict[str, Any]) -> None:
    """Reject a typo in a case, so it can't pass by quietly testing nothing."""
    case_id = raw.get("id", "?")
    unknown = set(raw) - CASE_KEYS
    if unknown:
        raise ValueError(f"case {case_id}: unknown keys {sorted(unknown)}")
    unknown = set(raw["expect"]) - EXPECT_KEYS
    if unknown:
        raise ValueError(f"case {case_id}: unknown expect keys {sorted(unknown)}")
    unknown = set(raw["expect"].get("summary", {})) - SUMMARY_KEYS
    if unknown:
        raise ValueError(f"case {case_id}: unknown summary keys {sorted(unknown)}")
    roles = {role for role, _ in raw.get("transcript", [])}
    if not roles <= ROLES:
        raise ValueError(f"case {case_id}: unknown transcript roles {sorted(roles - ROLES)}")


def evaluate(case: PostcallCase, profile: dict[str, str]) -> CaseReport:
    """Run every item that applies to ``case``."""
    report = CaseReport(case.id)

    start = time.perf_counter()
    decision = decide_outcome(
        case.report,
        case.ended_reason,
        case.business_lines,
        pause_capped=case.pause_capped,
        call_days=case.call_days,
    )
    report.outcome_ms = (time.perf_counter() - start) * 1000
    expected = case.expect["outcome"]
    if decision.outcome == expected:
        report.results.append(ItemResult("10 outcome", "pass", decision.outcome))
    elif case.known_gap:
        report.known_gap_booked = decision.outcome in NEEDS_EVIDENCE
        report.results.append(
            ItemResult(
                "10 outcome",
                "known_gap",
                f"got {decision.outcome!r}, expected {expected!r}: {case.known_gap}",
            )
        )
    else:
        report.wrong_booked = decision.outcome in NEEDS_EVIDENCE
        report.results.append(
            ItemResult(
                "10 outcome",
                "fail",
                f"got {decision.outcome!r} ({decision.reason}), expected {expected!r}",
            )
        )

    got = spoken_fields(profile, case.agent_lines)
    want = sorted(case.expect["spoken"])
    if got == want:
        report.results.append(ItemResult("12 shared", "pass", ", ".join(got) or "none"))
    else:
        missed = sorted(set(want) - set(got))
        extra = sorted(set(got) - set(want))
        report.results.append(ItemResult("12 shared", "fail", f"missed {missed}, extra {extra}"))

    if "summary" in case.expect:
        accepted = (
            case.report is not None
            and decision.outcome in NEEDS_EVIDENCE
            and decision.outcome == case.report.outcome.value
        )
        shown = (
            confirmed_details(case.report.details, case.business_lines, call_days=case.call_days)
            if accepted and case.report is not None
            else OutcomeDetails()
        )
        report.results.extend(summary_results(case.expect["summary"], shown))
    if "guardrail_breach" in case.expect:
        flag = "breach" if case.expect["guardrail_breach"] else "no breach"
        report.results.append(ItemResult("13 guardrail", "not_built", f"expected: {flag}"))
    return report


def summary_results(expected: dict[str, Any], shown: OutcomeDetails) -> list[ItemResult]:
    """Item 11 for one case: the shown details against ``expect.summary``."""
    results = []
    wrong = [
        f"{field} shows {getattr(shown, field)!r}, expected {expected[field]!r}"
        for field in SUMMARY_FIELDS
        if field in expected and getattr(shown, field) != expected[field]
    ]
    for phrase in expected.get("must_not_mention", []):
        field = FIELD_PHRASES.get(phrase.casefold())
        values = [getattr(shown, field)] if field else list(shown.model_dump().values())
        said = [v for v in values if v and phrase.casefold() in str(v).casefold()]
        if (field and getattr(shown, field)) or (not field and said):
            wrong.append(f"shows {phrase!r}")
    if any(f in expected for f in SUMMARY_FIELDS) or "must_not_mention" in expected:
        shown_text = json.dumps(shown.model_dump(exclude_none=True))
        results.append(
            ItemResult("11 summary", "fail" if wrong else "pass", "; ".join(wrong) or shown_text)
        )
    if "open_items_any_of" in expected:
        words = ", ".join(expected["open_items_any_of"])
        results.append(ItemResult("11 summary", "in_call", f"open items should mention: {words}"))
    return results


def percentile(values: list[float], fraction: float) -> float:
    """The value at ``fraction`` (0-1) of the sorted values, nearest-rank."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, round(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]
