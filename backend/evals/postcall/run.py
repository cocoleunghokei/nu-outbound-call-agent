"""Post-call eval report. From backend/: ``uv run python -m evals.postcall.run``.

Free: no API calls, no database.

Prints every case's result per evaluation item, the number of wrong "booked" outcomes (the
worst error; the target is 0), and the P50 / P95 time of the outcome decision. Items 11
(summary) and 13 (money guardrail) are listed as "not built" with their expected answers.
Exit 0 when nothing fails, 1 otherwise.

The latency here is the rule itself, in-process. The end-to-end time from the call ending
to ``call.ended`` reaching the browser (the jobs worker polls every 2 s) is measured in the
integration test, not here.
"""

import sys
from collections import Counter

from evals.postcall.checks import evaluate, load_cases, percentile


def main() -> int:
    profile, cases = load_cases()
    reports = [evaluate(case, profile) for case in cases]

    counts: Counter[tuple[str, str]] = Counter()
    for case, report in zip(cases, reports, strict=True):
        print(f"{case.id}: {case.about}")
        for result in report.results:
            counts[(result.item, result.status)] += 1
            mark = {"pass": "PASS", "fail": "FAIL", "not_built": "TODO"}[result.status]
            print(f"    {mark} {result.item}: {result.detail}")

    print()
    for item in ("10 outcome", "12 shared", "11 summary", "13 guardrail"):
        passed, failed = counts[(item, "pass")], counts[(item, "fail")]
        pending = counts[(item, "not_built")]
        if passed or failed:
            print(f"{item}: {passed}/{passed + failed} passed")
        if pending:
            print(f"{item}: {pending} cases ready, not built yet")
    wrong_booked = sum(report.wrong_booked for report in reports)
    print(f"wrong 'booked' outcomes: {wrong_booked} (target 0)")
    times = [report.outcome_ms for report in reports]
    print(
        f"outcome decision time: P50 {percentile(times, 0.5):.3f} ms,"
        f" P95 {percentile(times, 0.95):.3f} ms"
    )
    failed_total = sum(n for (_, status), n in counts.items() if status == "fail")
    return 1 if failed_total else 0


if __name__ == "__main__":
    sys.exit(main())
