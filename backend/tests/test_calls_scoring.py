"""Tests for scoring the heard offer against the brief's rules (``app/calls/scoring.py``).

The boundaries come from the in-call eval: C4-05 accepted exactly 3pm for "before 3pm", and
C4-04 took Friday 2pm as the fallback "Friday morning (before noon)". Fake data only.
"""

from typing import Any

import pytest

from app.calls.scoring import (
    check_item,
    check_rule,
    fix_offer_year,
    same_person,
    score_offer,
    score_tool_call,
)

BEFORE_3PM = {"kind": "time", "latest": "14:59"}
THURSDAY = {"kind": "date", "on": "2026-11-19"}
FRIDAY = {"kind": "date", "on": "2026-11-20"}
MORNING = {"kind": "time", "latest": "11:59"}

# Brief A of backend/evals/incall/cases.json, with the rules the parser would add.
BRIEF_A: dict[str, Any] = {
    "constraints": [
        {"text": "On Thursday 19 November", "rules": [THURSDAY]},
        {"text": "Before 3pm", "rules": [BEFORE_3PM]},
    ],
    "preferences": [
        {"text": "With Dr. Rivera", "rules": [{"kind": "person", "name": "Dr. Rivera"}]}
    ],
    "fallbacks": [
        {"text": "Friday 20 November, morning (before noon)", "rules": [FRIDAY, MORNING]}
    ],
    "never_agree": [{"text": "A cancellation or no-show fee"}],
}


# --- one rule ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("time", "expected"),
    [("14:00", "pass"), ("14:59", "pass"), ("15:00", "fail"), ("16:00", "fail"), ("00:00", "pass")],
)
def test_before_3pm_is_latest_1459(time: str, expected: str) -> None:
    assert check_rule(BEFORE_3PM, {"time": time}) == expected


@pytest.mark.parametrize(
    ("time", "expected"),
    [("10:00", "pass"), ("11:59", "pass"), ("12:00", "fail"), ("14:00", "fail")],
)
def test_morning_ends_before_noon(time: str, expected: str) -> None:
    assert check_rule(MORNING, {"time": time}) == expected


def test_a_time_window_is_inclusive_at_both_ends() -> None:
    window = {"kind": "time", "earliest": "18:30", "latest": "20:00"}
    assert check_rule(window, {"time": "18:30"}) == "pass"
    assert check_rule(window, {"time": "20:00"}) == "pass"
    assert check_rule(window, {"time": "18:29"}) == "fail"
    assert check_rule(window, {"time": "20:01"}) == "fail"
    assert check_rule({"kind": "time", "earliest": "18:00"}, {"time": "23:00"}) == "pass"


def test_dates_exact_and_ranges() -> None:
    assert check_rule(THURSDAY, {"date": "2026-11-19"}) == "pass"
    assert check_rule(THURSDAY, {"date": "2026-11-20"}) == "fail"
    weekend = {"kind": "date", "earliest": "2026-11-21", "latest": "2026-11-22"}
    assert check_rule(weekend, {"date": "2026-11-22"}) == "pass"
    assert check_rule(weekend, {"date": "2026-11-23"}) == "fail"
    assert check_rule(weekend, {"date": "2026-11-20"}) == "fail"


def test_party_size() -> None:
    six = {"kind": "party_size", "exactly": 6}
    assert check_rule(six, {"party_size": 6}) == "pass"
    assert check_rule(six, {"party_size": 4}) == "fail"
    small = {"kind": "party_size", "min": 2, "max": 4}
    assert check_rule(small, {"party_size": 4}) == "pass"
    assert check_rule(small, {"party_size": 5}) == "fail"
    assert check_rule(six, {"party_size": True}) == "unknown"  # a bool is not a count


@pytest.mark.parametrize(
    ("wanted", "offered", "same"),
    [
        ("Dr. Rivera", "Dr. Rivera", True),
        ("Dr. Rivera", "Rivera", True),
        ("Dr. Rivera", "doctor rivera", True),
        ("Dr. Rivera", "Dr. Maria Rivera", True),
        ("Dr. Maria Rivera", "Dr. Rivera", True),
        ("Dr. Rivera", "Dr. Patel", False),
        ("Dr. Maria Rivera", "Dr. Ana Rivera", False),
        ("Dr.", "Dr. Patel", False),  # a title alone names nobody
    ],
)
def test_same_person(wanted: str, offered: str, same: bool) -> None:
    assert same_person(wanted, offered) is same


def test_a_person_rule() -> None:
    rivera = {"kind": "person", "name": "Dr. Rivera"}
    assert check_rule(rivera, {"person": "Rivera"}) == "pass"
    assert check_rule(rivera, {"person": "Dr. Patel"}) == "fail"
    assert check_rule(rivera, {"person": "Dr."}) == "unknown"


@pytest.mark.parametrize(
    ("rule", "offer"),
    [
        (BEFORE_3PM, {}),  # the offer doesn't say a time
        (BEFORE_3PM, {"date": "2026-11-19"}),
        (BEFORE_3PM, {"time": "3pm"}),  # malformed: unknown, never a crash
        ({"kind": "time"}, {"time": "15:00"}),  # a rule with no bound
        ({"kind": "date"}, {"date": "2026-11-19"}),
        ({"kind": "party_size"}, {"party_size": 6}),
        ({"kind": "price", "max": 50}, {"time": "15:00"}),  # a kind this version doesn't know
        ({"kind": "time", "latest": "late"}, {"time": "15:00"}),
    ],
)
def test_missing_or_malformed_values_are_unknown(
    rule: dict[str, Any], offer: dict[str, Any]
) -> None:
    assert check_rule(rule, offer) == "unknown"


# --- one item ------------------------------------------------------------------------------


def test_an_item_passes_only_when_every_rule_passes() -> None:
    item = {"text": "Friday morning", "rules": [FRIDAY, MORNING]}
    assert check_item(item, {"date": "2026-11-20", "time": "10:00"}) == "pass"
    assert check_item(item, {"date": "2026-11-20", "time": "14:00"}) == "fail"  # C4-04
    assert check_item(item, {"date": "2026-11-19", "time": "10:00"}) == "fail"
    assert check_item(item, {"date": "2026-11-20"}) == "unknown"  # no time said yet
    assert check_item(item, {"time": "14:00"}) == "fail"  # one broken rule is enough


def test_an_item_without_rules_is_unknown() -> None:
    assert check_item({"text": "A quiet table"}, {"time": "19:00"}) == "unknown"
    assert check_item({"text": "A quiet table", "rules": []}, {"time": "19:00"}) == "unknown"


# --- the whole brief -----------------------------------------------------------------------


def test_c4_05_exactly_3pm_breaks_before_3pm() -> None:
    score = score_offer(BRIEF_A, {"date": "2026-11-19", "time": "15:00", "person": "Dr. Rivera"})
    assert score.fits is False
    assert score.broken_constraint == "Before 3pm"
    assert score.matches_fallback is False


def test_c4_04_friday_2pm_is_not_the_friday_morning_fallback() -> None:
    score = score_offer(BRIEF_A, {"date": "2026-11-20", "time": "14:00", "person": "Dr. Rivera"})
    assert score.fits is False
    assert score.broken_constraint == "On Thursday 19 November"
    assert score.matches_fallback is False


def test_c4_02_friday_10am_matches_the_fallback() -> None:
    score = score_offer(BRIEF_A, {"date": "2026-11-20", "time": "10:00"})
    assert score.fits is False
    assert score.matches_fallback is True


def test_c4_01_thursday_2pm_with_dr_rivera_fits() -> None:
    score = score_offer(BRIEF_A, {"date": "2026-11-19", "time": "14:00", "person": "Dr. Rivera"})
    assert score.fits is True
    assert score.missed_preferences == ()


def test_c4_06_dr_patel_fits_but_misses_the_preference() -> None:
    score = score_offer(BRIEF_A, {"date": "2026-11-19", "time": "14:00", "person": "Dr. Patel"})
    assert score.fits is True
    assert score.missed_preferences == ("With Dr. Rivera",)


def test_an_offer_without_the_day_is_unknown_not_a_fit() -> None:
    score = score_offer(BRIEF_A, {"time": "14:00"})
    assert score.fits is None  # the time fits, but the day wasn't said
    assert score.matches_fallback is False  # 2pm is no morning, whatever the day
    assert score_offer(BRIEF_A, {"time": "10:00"}).matches_fallback is None  # Friday? unknown


def test_a_text_only_constraint_leaves_the_fit_unknown() -> None:
    brief = {"constraints": [{"text": "Before 3pm", "rules": [BEFORE_3PM]}, {"text": "Quiet"}]}
    assert score_offer(brief, {"time": "14:00"}).fits is None
    assert score_offer(brief, {"time": "16:00"}).fits is False  # one known break is enough


def test_a_brief_with_nothing_typed_fits_anything() -> None:
    # Q8 (CASES.md): the opening line is not a rule, so with no typed constraints nothing breaks.
    score = score_offer({"constraints": [], "fallbacks": []}, {"time": "20:30"})
    assert score.fits is True
    assert score.matches_fallback is False


def test_a_brief_without_rules_scores_unknown_everywhere() -> None:
    # Briefs stored before PR 1 have text only: Python stays out and the model decides.
    text_only = {
        "constraints": [{"text": "Before 3pm"}],
        "preferences": [{"text": "Window seat"}],
        "fallbacks": [{"text": "Friday"}],
    }
    score = score_offer(text_only, {"date": "2026-11-19", "time": "16:00"})
    assert score.fits is None
    assert score.matches_fallback is None
    assert score.missed_preferences == ()


def test_score_tool_call_needs_a_brief_and_an_offer() -> None:
    offer = {"offer": {"time": "15:00"}}
    assert score_tool_call(None, offer) is None
    assert score_tool_call(BRIEF_A, {}) is None
    assert score_tool_call(BRIEF_A, {"offer": {}}) is None
    assert score_tool_call(BRIEF_A, {"offer": "3pm"}) is None
    score = score_tool_call(BRIEF_A, offer)
    assert score is not None and score.fits is False


# --- the year the model guessed --------------------------------------------------------------
# Eval run 2026-11-18T0303Z: the brief never said the year, the model wrote 2025-11-19, and the
# gates refused a correct accept of Thursday 2pm (C4-01, C4-02, C4-15, C4-17, T-01).


def test_a_wrong_year_is_moved_to_the_briefs_year() -> None:
    offer = {"date": "2025-11-19", "time": "14:00", "person": "Dr. Rivera"}
    assert fix_offer_year(BRIEF_A, offer)["date"] == "2026-11-19"
    assert score_offer(BRIEF_A, offer).fits is True


def test_a_wrong_year_still_matches_the_fallback() -> None:
    score = score_offer(BRIEF_A, {"date": "2025-11-20", "time": "10:00"})
    assert score.matches_fallback is True


def test_a_wrong_year_does_not_hide_a_broken_rule() -> None:
    score = score_offer(BRIEF_A, {"date": "2025-11-19", "time": "15:00"})
    assert score.broken_constraint == "Before 3pm"


def test_a_date_across_new_year_goes_to_the_next_january() -> None:
    brief = {
        "constraints": [{"text": "On 30 December", "rules": [{"kind": "date", "on": "2026-12-30"}]}]
    }
    assert fix_offer_year(brief, {"date": "2026-01-02"})["date"] == "2027-01-02"


def test_the_year_is_left_alone_when_the_brief_names_no_date() -> None:
    brief = {"constraints": [{"text": "Before 3pm", "rules": [BEFORE_3PM]}]}
    offer = {"date": "2025-11-19"}
    assert fix_offer_year(brief, offer) is offer


def test_a_malformed_offer_date_is_left_alone() -> None:
    offer = {"date": "Thursday"}
    assert fix_offer_year(BRIEF_A, offer) is offer


# --- what the offer leaves out (missing_detail_gate) --------------------------------------


def test_c4_05_a_friday_fallback_with_no_time_is_missing_the_time() -> None:
    # The 03:54 run: after "3pm is the earliest", the agent took the Friday morning fallback
    # with offer {date: 2026-11-20} and no time. Python couldn't check "morning".
    score = score_offer(BRIEF_A, {"date": "2026-11-20", "person": "Dr. Rivera"})
    assert score.matches_fallback is None
    assert score.missing_to_settle("take_fallback") == ("time",)
    # Friday breaks "On Thursday", so an accept rests on the fallback, which needs the time.
    assert score.fits is False
    assert score.missing_to_settle("accept") == ("time",)


def test_nothing_is_missing_once_python_can_tell() -> None:
    fits = score_offer(BRIEF_A, {"date": "2026-11-19", "time": "14:00"})
    friday_10 = score_offer(BRIEF_A, {"date": "2026-11-20", "time": "10:00"})
    low = score_offer(BRIEF_A, {"date": "2026-11-19", "time": "15:00"})  # fails, not unknown
    for score in (fits, friday_10):
        assert score.missing_to_settle("accept") == ()
        assert score.missing_to_settle("take_fallback") == ()
    assert low.missing_to_settle("accept") == ()  # the Low-offer gate's job


def test_every_missing_field_is_named_once_in_rule_order() -> None:
    score = score_offer(BRIEF_A, {"person": "Dr. Rivera"})
    assert score.missing_to_settle("accept") == ("date", "time")
    assert score.missing_to_settle("counter") == ()  # only settling actions need them


def test_text_only_items_never_count_as_missing() -> None:
    text_only = {"constraints": [{"text": "Somewhere quiet"}], "fallbacks": [{"text": "Patio"}]}
    score = score_offer(text_only, {"time": "19:00"})
    assert score.missing_to_settle("accept") == ()
    assert score.missing_to_settle("take_fallback") == ()


def test_a_malformed_value_counts_as_missing() -> None:
    score = score_offer(BRIEF_A, {"date": "2026-11-20", "time": "morning"})
    assert score.missing_to_settle("take_fallback") == ("time",)
