"""Asked-vs-got rows for the result page (app/postcall/result_items.py)."""

from typing import Any

import pytest

from app.contracts.generated import OutcomeDetails, ReportOutcomeArgs, ResultItemRef
from app.postcall.result_items import (
    SeqTurn,
    amount_said,
    asked_text,
    breaks_rules,
    find_evidence,
    shown_result_items,
)

# The coco resto cake call, with the speech-to-text fragments as they arrive.
TURNS: list[SeqTurn] = [
    (1, "agent", "Hi. I'm an AI assistant calling on behalf of Hokey."),
    (2, "agent", "It's for a birthday, so we'd like a happy birthday cake topper,"),
    (3, "business", "Yeah. We have"),
    (4, "business", "all of those"),
    (5, "business", "except Mochi."),
    (6, "business", "Is there"),
    (7, "business", "is marshmallow okay?"),
    (8, "agent", "Marshmallow is fine. Thank you."),
    (9, "business", "We can't do 10 AM. Noon is the earliest for pickup."),
    (10, "business", "The ice pack is $3 extra."),
    (11, "agent", "Can it be free?"),
    (12, "business", "No, we can't waive that."),
]

BRIEF: dict[str, Any] = {
    "constraints": [
        {"text": "Pick up by 10am", "rules": [{"kind": "time", "latest": "10:00"}]},
        {"text": "Topper, candles and a knife included"},
    ],
    "preferences": [{"text": "Mochi cake"}],
    "fallbacks": [{"text": "Pickup at 11am", "rules": [{"kind": "time", "latest": "11:59"}]}],
    "task_vars": {"cake": {"literal": "birthday cake"}, "name": {"field_ref": "full_name"}},
}


def item(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ref": {"kind": "preference", "index": 0},
        "label": "Cake",
        "got": "Marshmallow cake",
        "status": "changed",
        "business_said": "We have all of those except mochi. Is marshmallow okay?",
    }
    return {**base, **changes}


def report(*items: dict[str, Any]) -> ReportOutcomeArgs:
    return ReportOutcomeArgs.model_validate(
        {"outcome": "booked", "business_confirmed": True, "result_items": list(items)}
    )


def rows(*items: dict[str, Any], details: OutcomeDetails | None = None, user_replied: bool = True):  # type: ignore[no-untyped-def]
    return shown_result_items(
        report(*items), BRIEF, TURNS, details or OutcomeDetails(), user_replied
    )


# --- evidence ---------------------------------------------------------------------------------


def test_a_quote_is_found_across_split_lines_and_gives_every_line_it_spans() -> None:
    quote = "We have all of those except mochi. Is marshmallow okay?"
    assert find_evidence(quote, TURNS) == [3, 4, 5, 6, 7]
    assert find_evidence("all of those except mochi", TURNS) == [4, 5]


def test_a_quote_may_leave_out_a_few_words_but_not_reorder_or_invent_any() -> None:
    # "Is there / is marshmallow okay?" quoted without the stumble.
    assert find_evidence("except mochi is marshmallow okay", TURNS) == [5, 6, 7]
    assert find_evidence("okay marshmallow is", TURNS) == []  # reordered
    assert find_evidence("except mochi is marshmallow cake okay", TURNS) == []  # an invented word
    # More than three words skipped is a different sentence.
    turns: list[SeqTurn] = [(1, "business", "ice pack one two three four extra")]
    assert find_evidence("ice pack extra", turns) == []
    assert find_evidence(
        "ice pack one two three extra", [(1, "business", "ice pack one two three extra")]
    ) == [1]


def test_matching_ignores_case_and_punctuation() -> None:
    assert find_evidence("THE ICE PACK IS 3 EXTRA", TURNS) == [10]


def test_a_quote_never_spans_an_agent_line() -> None:
    # Lines 7 and 9 are both the business's, but the agent spoke in between.
    assert find_evidence("is marshmallow okay we can't do 10 AM", TURNS) == []


def test_a_quote_the_business_never_said_has_no_evidence() -> None:
    assert find_evidence("Delivery is on us", TURNS) == []
    assert (
        find_evidence("Marshmallow is fine", TURNS) == []
    )  # the agent said that, not the business


@pytest.mark.parametrize("quote", [None, "", "   ", "yes", "Yeah."])
def test_a_quote_of_fewer_than_two_words_proves_nothing(quote: str | None) -> None:
    assert find_evidence(quote, TURNS) == []


# --- amounts ----------------------------------------------------------------------------------


def test_amount_said_in_digits_and_words() -> None:
    assert amount_said(300, TURNS)  # "$3"
    turns: list[SeqTurn] = [
        (1, "business", "That is three dollars."),
        (2, "business", "Or $12.50 for two."),
    ]
    assert amount_said(300, turns)
    assert amount_said(1250, turns)
    assert not amount_said(500, turns)
    twenty_five: list[SeqTurn] = [(1, "business", "It's twenty five dollars")]
    assert amount_said(2500, twenty_five)


def test_an_amount_only_the_agent_said_does_not_count() -> None:
    assert not amount_said(500, [(1, "agent", "I'll pay five dollars."), (2, "business", "Okay.")])


# --- the Asked column -------------------------------------------------------------------------


def test_asked_comes_from_the_brief_never_from_the_agent() -> None:
    assert asked_text(BRIEF, ResultItemRef(kind="preference", index=0)) == "Mochi cake"  # type: ignore[arg-type]
    assert (
        asked_text(BRIEF, ResultItemRef(kind="constraint", index=1))
        == "Topper, candles and a knife included"
    )  # type: ignore[arg-type]
    assert asked_text(BRIEF, ResultItemRef(kind="fallback", index=0)) == "Pickup at 11am"  # type: ignore[arg-type]
    assert asked_text(BRIEF, ResultItemRef(kind="task_var", key="cake")) == "birthday cake"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "ref",
    [
        {"kind": "extra"},
        {"kind": "preference", "index": 5},  # out of range
        {"kind": "preference"},  # no index
        {"kind": "task_var", "key": "name"},  # a profile value is never shown
        {"kind": "task_var", "key": "missing"},
    ],
)
def test_asked_is_none_when_it_cannot_be_told(ref: dict[str, Any]) -> None:
    assert asked_text(BRIEF, ResultItemRef.model_validate(ref)) is None


def test_asked_is_none_without_a_brief() -> None:
    assert asked_text(None, ResultItemRef(kind="preference", index=0)) is None  # type: ignore[arg-type]


# --- rules ------------------------------------------------------------------------------------


def test_an_agreed_time_after_a_latest_rule_breaks_it() -> None:
    ref = ResultItemRef(kind="constraint", index=0)  # type: ignore[arg-type]
    assert breaks_rules(BRIEF, ref, OutcomeDetails(time="12:00"))
    assert not breaks_rules(BRIEF, ref, OutcomeDetails(time="10:00"))
    assert not breaks_rules(BRIEF, ref, OutcomeDetails())  # nothing agreed to compare


def test_a_time_that_is_not_hh_mm_cannot_be_compared() -> None:
    ref = ResultItemRef(kind="constraint", index=0)  # type: ignore[arg-type]
    assert not breaks_rules(BRIEF, ref, OutcomeDetails(time="noon"))


def test_date_rules() -> None:
    brief = {"constraints": [{"text": "x", "rules": [{"kind": "date", "on": "2026-11-25"}]}]}
    ref = ResultItemRef(kind="constraint", index=0)  # type: ignore[arg-type]
    assert not breaks_rules(brief, ref, OutcomeDetails(date="2026-11-25"))
    assert breaks_rules(brief, ref, OutcomeDetails(date="2026-11-26"))
    ranged = {
        "constraints": [
            {
                "text": "x",
                "rules": [{"kind": "date", "earliest": "2026-11-25", "latest": "2026-11-27"}],
            }
        ]
    }
    assert not breaks_rules(ranged, ref, OutcomeDetails(date="2026-11-27"))
    assert breaks_rules(ranged, ref, OutcomeDetails(date="2026-11-28"))


# --- the rows ---------------------------------------------------------------------------------


def test_a_verified_row_gets_asked_from_the_brief_and_evidence_from_the_transcript() -> None:
    (row,) = rows(item(reason="Mochi isn't available.", approved_by="user"))
    assert row.model_dump() == {
        "label": "Cake",
        "asked": "Mochi cake",
        "got": "Marshmallow cake",
        "status": "changed",
        "reason": "Mochi isn't available.",
        "approved_by": "user",
        "cost_cents": None,
        "evidence": [3, 4, 5, 6, 7],
        "verified": True,
    }


def test_the_ice_pack_fee_is_an_extra_row_with_a_checked_cost() -> None:
    fee = item(
        ref={"kind": "extra"},
        label="Ice pack",
        got="+$3.00",
        status="extra",
        cost_cents=300,
        business_said="The ice pack is $3 extra.",
    )
    (row,) = rows(fee)
    assert (row.asked, row.cost_cents, row.evidence, row.verified) == (None, 300, [10], True)


def test_an_unverified_changed_row_is_left_out() -> None:
    assert rows(item(business_said="Delivery is on us.")) == []
    assert rows(item(business_said=None)) == []


@pytest.mark.parametrize("status", ["changed", "not_met", "extra"])
def test_unverified_rows_with_any_other_status_are_left_out(status: str) -> None:
    assert rows(item(status=status, business_said="They never said this.")) == []


def test_an_unverified_as_asked_row_is_shown_marked() -> None:
    extras = item(
        ref={"kind": "constraint", "index": 1},
        label="Topper, candles, knife",
        got="All included",
        status="as_asked",
        business_said=None,
    )
    (row,) = rows(extras)
    assert (row.status, row.verified, row.evidence, row.asked) == (
        "as_asked",
        False,
        [],
        "Topper, candles and a knife included",
    )


def test_a_cost_the_business_never_said_makes_the_row_unverified() -> None:
    fee = item(
        ref={"kind": "extra"},
        label="Ice pack",
        got="+$5.00",
        status="extra",
        cost_cents=500,
        business_said="The ice pack is $3 extra.",  # the quote is real, the amount is not
    )
    assert rows(fee) == []


def test_as_asked_becomes_changed_when_the_agreed_time_breaks_the_rule() -> None:
    pickup = item(
        ref={"kind": "constraint", "index": 0},
        label="Pickup",
        got="Wed 10:00 AM",
        status="as_asked",
        business_said="Noon is the earliest for pickup.",
    )
    (row,) = rows(pickup, details=OutcomeDetails(time="12:00"))
    assert (row.status, row.verified) == ("changed", True)
    (kept,) = rows(pickup, details=OutcomeDetails(time="10:00"))
    assert kept.status == "as_asked"


def test_an_unverified_row_forced_to_changed_is_then_left_out() -> None:
    pickup = item(
        ref={"kind": "constraint", "index": 0},
        got="Wed 10:00 AM",
        status="as_asked",
        business_said=None,
    )
    assert rows(pickup, details=OutcomeDetails(time="12:00")) == []


def test_approved_by_user_needs_a_reply_from_the_user() -> None:
    (row,) = rows(item(approved_by="user"), user_replied=False)
    assert row.approved_by == "none"
    (kept,) = rows(item(approved_by="brief"), user_replied=False)
    assert kept.approved_by == "brief"
    (unset,) = rows(item(), user_replied=False)
    assert unset.approved_by is None


def test_blank_text_after_trimming_is_left_out_and_the_rest_is_trimmed() -> None:
    assert rows(item(label="   ")) == []
    (row,) = rows(item(label=" Cake ", got=" Marshmallow cake ", reason="  "))
    assert (row.label, row.got, row.reason) == ("Cake", "Marshmallow cake", None)


def test_rows_keep_the_agents_order_and_a_report_without_rows_gives_none() -> None:
    first, second = item(label="Cake"), item(label="Again", ref={"kind": "preference", "index": 0})
    assert [r.label for r in rows(first, second)] == ["Cake", "Again"]
    assert rows() == []
    assert shown_result_items(None, BRIEF, TURNS, OutcomeDetails(), True) == []


def test_rows_without_a_stored_brief_still_show_with_no_asked() -> None:
    (row,) = shown_result_items(report(item()), None, TURNS, OutcomeDetails(), True)
    assert row.asked is None and row.verified
