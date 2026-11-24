"""The post-call eval cases (evals/postcall/cases.json) run in CI: free, no database."""

from dataclasses import replace

import pytest

from evals.postcall.checks import PostcallCase, _check_keys, evaluate, load_cases, percentile

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


def _raw_case(**changes: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "id": "typo",
        "transcript": [["business", "Sorry, we're full."]],
        "expect": {"outcome": "needs_follow_up", "spoken": []},
    }
    raw.update(changes)
    return raw


# Regression (#63 review): a typo in a case used to drop its data silently, so a refusal
# case could pass while testing nothing.
@pytest.mark.parametrize(
    "raw",
    [
        _raw_case(transcript=[["Business", "Sorry, we're full."]]),
        _raw_case(transcipt=[]),
        _raw_case(expect={"outcome": "needs_follow_up", "spoken": [], "sumary": {}}),
        _raw_case(
            expect={"outcome": "needs_follow_up", "spoken": [], "summary": {"stafname": "M"}}
        ),
    ],
    ids=["role", "case key", "expect key", "summary key"],
)
def test_a_typo_in_a_case_is_rejected(raw: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="unknown"):
        _check_keys(raw)


def test_a_wrong_cancelled_counts_like_a_wrong_booked() -> None:
    # A wrong "cancelled" misleads the user as much as a wrong "booked".
    cancelled = next(c for c in CASES if c.id == "cancelled_with_number")
    wrongly_expected = replace(cancelled, expect={**cancelled.expect, "outcome": "needs_follow_up"})
    result = evaluate(wrongly_expected, PROFILE)
    assert result.results[0].status == "fail"
    assert result.wrong_booked


def test_the_summary_check_catches_a_detail_that_shows_but_was_never_said() -> None:
    # The checker itself: if the expected summary wrongly wanted the made-up staff name,
    # item 11 must fail, because the page doesn't show it.
    case = next(c for c in CASES if c.id == "summary_invented_details")
    summary = {**case.expect["summary"], "staff_name": "Marco", "must_not_mention": []}
    result = evaluate(replace(case, expect={**case.expect, "summary": summary}), PROFILE)
    item = next(r for r in result.results if r.item == "11 summary")
    assert item.status == "fail"
    assert "staff_name shows None" in item.detail
