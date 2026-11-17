"""Post-call eval checks, shared by the report (``run.py``) and the offline test
(``tests/test_postcall_evals.py``).

Each case in ``cases.json`` is one ended call. The checks follow the AI evaluation scope:

- item 10, outcome (PO-1): ``decide.decide_outcome`` must give ``expect.outcome``. A wrong
  "booked" (or "cancelled" / "rescheduled") is the worst error and is counted on its own.
- item 12, what was shared (PO-3): ``decide.spoken_fields`` must give exactly
  ``expect.spoken``. A missed field breaks the trust promise; an extra one alarms the user.
- item 11, summary (PO-2) and item 13, the money guardrail: not built yet. Their
  expectations are reported as "not built" so they are ready when the code is.

No database, no network and no paid API: these run in CI.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.contracts.generated import ReportOutcomeArgs
from app.postcall.decide import NEEDS_EVIDENCE, decide_outcome, spoken_fields

CASES_PATH = Path(__file__).with_name("cases.json")

Status = Literal["pass", "fail", "not_built"]

CASE_KEYS = frozenset(
    {"id", "about", "report", "ended_reason", "pause_capped", "transcript", "events", "expect"}
)
EXPECT_KEYS = frozenset({"outcome", "spoken", "summary", "guardrail_breach"})
ROLES = frozenset({"agent", "business"})


@dataclass(frozen=True)
class PostcallCase:
    """One ended call and its right answers."""

    id: str
    about: str
    report: ReportOutcomeArgs | None
    ended_reason: str
    pause_capped: bool
    agent_lines: list[str]
    business_lines: list[str]
    events: list[dict[str, Any]]
    expect: dict[str, Any]


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
                ended_reason=raw.get("ended_reason", ""),
                pause_capped=bool(raw.get("pause_capped", False)),
                agent_lines=[text for role, text in transcript if role == "agent"],
                business_lines=[text for role, text in transcript if role == "business"],
                events=list(raw.get("events", [])),
                expect=dict(raw["expect"]),
            )
        )
    return profile, cases


def _check_keys(raw: dict[str, Any]) -> None:
    """Reject a typo in a case, so it can't pass by quietly testing nothing."""
    case_id = raw.get("id", "?")
    unknown = set(raw) - CASE_KEYS
    if unknown:
        raise ValueError(f"case {case_id}: unknown keys {sorted(unknown)}")
    unknown = set(raw["expect"]) - EXPECT_KEYS
    if unknown:
        raise ValueError(f"case {case_id}: unknown expect keys {sorted(unknown)}")
    roles = {role for role, _ in raw.get("transcript", [])}
    if not roles <= ROLES:
        raise ValueError(f"case {case_id}: unknown transcript roles {sorted(roles - ROLES)}")


def evaluate(case: PostcallCase, profile: dict[str, str]) -> CaseReport:
    """Run every item that applies to ``case``."""
    report = CaseReport(case.id)

    start = time.perf_counter()
    decision = decide_outcome(
        case.report, case.ended_reason, case.business_lines, pause_capped=case.pause_capped
    )
    report.outcome_ms = (time.perf_counter() - start) * 1000
    expected = case.expect["outcome"]
    if decision.outcome == expected:
        report.results.append(ItemResult("10 outcome", "pass", decision.outcome))
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
        report.results.append(
            ItemResult("11 summary", "not_built", json.dumps(case.expect["summary"]))
        )
    if "guardrail_breach" in case.expect:
        flag = "breach" if case.expect["guardrail_breach"] else "no breach"
        report.results.append(ItemResult("13 guardrail", "not_built", f"expected: {flag}"))
    return report


def percentile(values: list[float], fraction: float) -> float:
    """The value at ``fraction`` (0-1) of the sorted values, nearest-rank."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, round(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]
