"""The post-call eval cases (evals/postcall/cases.json) run in CI: free, no database."""

import pytest

from evals.postcall.checks import PostcallCase, evaluate, load_cases, percentile

PROFILE, CASES = load_cases()


def test_case_ids_are_unique() -> None:
    ids = [case.id for case in CASES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_outcome_and_shared_fields_match_the_expected_answer(case: PostcallCase) -> None:
    failures = [r for r in evaluate(case, PROFILE).results if r.status == "fail"]
    assert failures == []


def test_no_case_is_wrongly_booked() -> None:
    # A wrong "booked" is the worst post-call error (evaluation item 10): the target is 0.
    assert [case.id for case in CASES if evaluate(case, PROFILE).wrong_booked] == []


def test_the_cases_cover_every_item_and_outcome() -> None:
    items = {r.item for case in CASES for r in evaluate(case, PROFILE).results}
    assert items == {"10 outcome", "11 summary", "12 shared", "13 guardrail"}
    outcomes = {case.expect["outcome"] for case in CASES}
    expected = {
        "booked",
        "cancelled",
        "rescheduled",
        "needs_follow_up",
        "refused_ai",
        "wrong_number",
        "no_answer",
    }
    assert outcomes == expected


def test_percentile_is_nearest_rank() -> None:
    assert percentile([], 0.5) == 0.0
    assert percentile([3.0, 1.0, 2.0], 0.5) == 2.0
    assert percentile([float(n) for n in range(1, 21)], 0.95) == 19.0
