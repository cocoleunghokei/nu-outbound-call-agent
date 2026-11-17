"""Pure post-call rules (app/postcall/decide.py): no database needed."""

from datetime import date
from typing import Any

import pytest

from app.contracts.generated import OutcomeDetails, ReportOutcomeArgs
from app.postcall.decide import (
    clauses,
    decide_outcome,
    has_confirmation_evidence,
    is_question,
    is_refusal,
    mentions_date,
    mentions_time,
    parse_time,
    says_confirmation_number,
    spoken_fields,
    valid_confirmation_number,
    valid_iso_date,
)


def report(**fields: Any) -> ReportOutcomeArgs:
    return ReportOutcomeArgs.model_validate({"business_confirmed": True, **fields})


BOOKED = report(outcome="booked", details={"date": "2026-10-24", "time": "19:30"})
CANCELLED = report(outcome="cancelled", details={"date": "2026-10-24", "time": "19:30"})
RESCHEDULED = report(outcome="rescheduled", details={"date": "2026-10-24", "time": "19:30"})


def outcome(rep: ReportOutcomeArgs, *lines: str) -> str:
    return decide_outcome(rep, "assistant-ended-call", list(lines)).outcome


# --- confirmations that count ------------------------------------------------------------


def test_time_and_date_in_one_clause_is_booked() -> None:
    assert outcome(BOOKED, "Got it, party of 2 at 7:30 on Saturday, October 24th.") == "booked"
    assert outcome(BOOKED, "You're booked for October 24 at 7:30pm.") == "booked"
    assert outcome(BOOKED, "See you on the 24th of October at 19:30.") == "booked"


def test_confirmation_number_is_booked() -> None:
    numbered = report(outcome="booked", details={"confirmation_number": "A1B2"})
    assert outcome(numbered, "Done. Your confirmation number is A1B2.") == "booked"


def test_periods_inside_times_and_dates_do_not_split_the_clause() -> None:
    later = report(outcome="booked", details={"date": "2026-10-24", "time": "20:00"})
    assert outcome(later, "Booked for 8 p.m. on Oct. 24, thanks.") == "booked"


def test_cancelled_and_rescheduled_use_the_same_rules() -> None:
    assert outcome(CANCELLED, "Okay, October 24 at 7:30 is cancelled.") == "cancelled"
    assert outcome(RESCHEDULED, "Moved to October 24 at 7:30, all set.") == "rescheduled"


# --- refusals and doubts: never booked (when in doubt, needs_follow_up) -------------------


@pytest.mark.parametrize(
    "lines",
    [
        # Steve's reproductions on 285058f
        ["We're fully booked on October 24 at 7:30."],
        ["October 24 at 7:30? Let me check.", "Sorry, we're full."],
        ["7:30 on October 24, but we're full."],
        # the first review's case
        ["Sorry, Saturday at 7:30 is full."],
        # refusal words the reviewers found missing
        ["No, October 24 at 7:30 doesn't work."],
        ["Unfortunately October 24 at 7:30 is taken."],
        ["We have nothing on October 24 at 7:30."],
        ["October 24 at 7:30 won't work."],
        ["October 24 at 7:30 isn't possible."],
        ["We're booked solid October 24 at 7:30."],
        ["We cant do October 24 at 7:30."],
        ["We're closed that night, October 24 at 7:30."],
        ["I can put you on the waitlist for October 24 at 7:30."],
        # time and date from different lines
        ["We open at 7:30.", "October 24? Let me check with my manager."],
        # "Oct." and "p.m." used to split clauses
        ["Oct. 24 at 7:30 p.m. is full."],
        # am/pm that contradicts the agreed 19:30
        ["See you at 7:30 am on October 24."],
        # a counteroffer in the same line as a refusal: the user checks
        ["7:30 is full, but 8pm on October 24 works."],
        # a question is never evidence
        ["You want October 24 at 7:30?"],
        # third review: a refusal on a LATER line undoes a readback
        ["So October 24 at 7:30.", "No, that doesn't work, we have no space."],
        ["Booked October 24 at 7:30. Wait, no, October 25."],
        # a question without a capital after it (speech-to-text drops it)
        ["October 24 at 7:30? let me check"],
        # refusal words added in the third review
        ["Not October 24 at 7:30, but 9 works."],
        ["October 24 at 7:30 is no longer available."],
        ["I'd have to check October 24 at 7:30 and call you back."],
        ["Sorry, we couldn't fit you October 24 at 7:30."],
        ["October 24 at 7:30 is gone."],
        ["October 24 at 7:30 is not going to work."],
        # a year said with the date must be the agreed one
        ["See you October 24, 2027 at 7:30."],
    ],
)
def test_refused_or_doubtful_slots_are_not_booked(lines: list[str]) -> None:
    assert outcome(BOOKED, *lines) == "needs_follow_up"


def test_cancel_question_then_cant_find_is_not_cancelled() -> None:
    lines = ["You want to cancel October 24 at 7:30? I can't find that reservation."]
    assert outcome(CANCELLED, *lines) == "needs_follow_up"


def test_reschedule_refusal_is_not_rescheduled() -> None:
    assert outcome(RESCHEDULED, "Sorry, October 24 at 7:30 is not available.") == "needs_follow_up"


def test_a_later_clean_line_still_confirms() -> None:
    lines = ["Sorry, 7 is full.", "But October 24 at 7:30 works, you're booked."]
    assert outcome(BOOKED, *lines) == "booked"
    # A refusal BEFORE the confirmation is fine.
    assert outcome(BOOKED, "Sorry, 7:30 is full.", "Sure, October 24 at 7:30.") == "booked"


def test_no_problem_still_confirms_but_not_a_problem_does_not() -> None:
    # A bare "no" is not a refusal word, so this still counts.
    assert outcome(BOOKED, "No problem, see you October 24 at 7:30.") == "booked"
    # "not" is, so this becomes needs_follow_up. Accepted: when in doubt, the user checks.
    assert outcome(BOOKED, "Not a problem, see you October 24 at 7:30.") == "needs_follow_up"
    # A refusal word after the confirmation, even a polite one, also sends it to the user.
    assert outcome(BOOKED, "October 24 at 7:30, done.", "Not at all, bye!") == "needs_follow_up"


# --- the confirmation number ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("number", "lines"),
    [
        ("1234", ["Our number is 555-1234."]),
        ("2026", ["That's Saturday October 24, 2026.", "We have nothing then."]),
        ("1500", ["Thanks for calling Luigi's at 1500 Main Street."]),
        ("1234", ["We have no record of 1234."]),
        ("1234", ["Your confirmation number is 12345."]),
        ("1234", ["The reference is 1234/5."]),
        ("ABCD", ["Abcd, how can I help?"]),
    ],
)
def test_confirmation_number_needs_a_cue_and_must_stand_alone(
    number: str, lines: list[str]
) -> None:
    numbered = report(outcome="booked", details={"confirmation_number": number})
    assert outcome(numbered, *lines) == "needs_follow_up"


@pytest.mark.parametrize(
    ("number", "line"),
    [
        ("A1B2", "Your confirmation number is A1B2."),
        ("ABCD", "Your reference is ABCD."),
        ("TB-1234", "Booking number TB-1234, see you then."),
        ("7781", "Conf 7781."),
    ],
)
def test_confirmation_number_with_a_cue_is_booked(number: str, line: str) -> None:
    numbered = report(outcome="booked", details={"confirmation_number": number})
    assert outcome(numbered, line) == "booked"


def test_says_confirmation_number() -> None:
    assert says_confirmation_number("your confirmation number is a1b2.", "A1B2")
    assert not says_confirmation_number("our number is 555-1234.", "1234")
    assert not says_confirmation_number("a1b2, see you.", "A1B2")


# --- the agent's details must be well-formed ------------------------------------------------


def test_malformed_details_are_not_evidence() -> None:
    # The contract accepts any string (so one odd field doesn't throw away the report);
    # decide.py checks each detail itself.
    weekday = OutcomeDetails(date="Saturday", time="19:30")
    assert not has_confirmation_evidence(weekday, ["Saturday at 7:30, see you."])
    tiny = OutcomeDetails(confirmation_number="24")
    assert not has_confirmation_evidence(tiny, ["Confirmation number 24."])


def test_loose_details_are_accepted_and_checked_here() -> None:
    loose = report(outcome="booked", details={"date": "2026-10-24", "time": "7:30 PM"})
    assert outcome(loose, "See you October 24 at 7:30pm.") == "booked"
    for details in ({"date": "Oct 24"}, {"confirmation_number": "ABCD"}):
        report(outcome="refused_ai", details=details)  # no ValidationError


def test_valid_iso_date_and_confirmation_number() -> None:
    assert valid_iso_date("2026-10-24") == date(2026, 10, 24)
    assert valid_iso_date("2026-02-30") is None
    assert valid_iso_date("Oct 24") is None
    assert valid_confirmation_number(" TB-1234 ") == "TB-1234"
    assert valid_confirmation_number("A1") is None
    assert valid_confirmation_number("ABCD") == "ABCD"
    assert valid_confirmation_number("A-1") is None


# --- other outcomes ------------------------------------------------------------------------


def test_booking_the_business_did_not_confirm_needs_follow_up() -> None:
    unconfirmed = report(
        outcome="booked", business_confirmed=False, details={"date": "2026-10-24", "time": "19:30"}
    )
    assert outcome(unconfirmed, "October 24 at 7:30, see you.") == "needs_follow_up"


def test_outcomes_that_arrange_nothing_are_taken_as_reported() -> None:
    for reported in ("refused_ai", "wrong_number", "needs_follow_up"):
        assert decide_outcome(report(outcome=reported), "", []).outcome == reported


def test_no_report_and_nobody_answered_is_no_answer() -> None:
    for reason in ("customer-did-not-answer", "customer-busy", "voicemail"):
        assert decide_outcome(None, reason, []).outcome == "no_answer"


def test_no_report_and_any_other_end_needs_follow_up_never_failed() -> None:
    for reason in ("assistant-ended-call", "pipeline-error", ""):
        assert decide_outcome(None, reason, []).outcome == "needs_follow_up"


def test_pause_cap_with_no_report_needs_follow_up() -> None:
    decision = decide_outcome(None, "customer-did-not-answer", [], pause_capped=True)
    assert decision.outcome == "needs_follow_up"
    assert "pause cap" in decision.reason


# --- building blocks -----------------------------------------------------------------------


def test_clauses_split_on_sentence_ends_only() -> None:
    assert clauses("Sorry. We're full! Anything else?") == [
        "Sorry.",
        "We're full!",
        "Anything else?",
    ]
    assert clauses("Oct. 24 at 8 p.m. works.") == ["Oct. 24 at 8 p.m. works."]
    assert clauses("7:30 is full, but 8pm works.") == ["7:30 is full, but 8pm works."]
    assert clauses("October 24 at 7:30? let me check") == ["October 24 at 7:30?", "let me check"]


def test_is_refusal_and_is_question() -> None:
    assert is_refusal("We're fully booked")
    assert is_refusal("We can’t do that")
    assert is_refusal("We cant do that")
    assert not is_refusal("Got it, see you at 7:30")
    assert is_refusal("No, that's too late")
    assert is_refusal("Wait, no, October 25")
    assert not is_refusal("No problem")
    assert not is_refusal("You're booked")  # a bare "booked" must still confirm
    assert is_question("October 24 at 7:30?  ")
    assert not is_question("October 24 at 7:30.")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("19:30", (19, 30)),
        ("7:30 PM", (19, 30)),
        ("7:30pm", (19, 30)),
        ("7pm", (19, 0)),
        ("7 p.m.", (19, 0)),
        ("12:15 am", (0, 15)),
        ("7", None),
        ("noon", None),
        ("25:00", None),
        (None, None),
    ],
)
def test_parse_time(value: str | None, expected: tuple[int, int] | None) -> None:
    assert parse_time(value) == expected


def test_mentions_time_accepts_matching_am_pm_and_24_hour_forms() -> None:
    for said in ("7:30", "7:30pm", "7:30 p.m.", "19:30"):
        assert mentions_time(f"see you at {said}.", (19, 30)), said
    assert mentions_time("see you at 7pm.", (19, 0))
    assert mentions_time("see you at 7 o'clock.", (19, 0))
    assert mentions_time("see you at 9:30am.", (9, 30))


def test_mentions_time_rejects_the_wrong_half_of_the_day_and_longer_numbers() -> None:
    assert not mentions_time("see you at 7:30 am.", (19, 30))
    assert not mentions_time("see you at 7:30pm.", (7, 30))
    assert not mentions_time("see you at 7am.", (19, 0))
    assert not mentions_time("we open at 17:30.", (7, 30))


def test_mentions_date_needs_month_and_day() -> None:
    day = date(2026, 10, 24)
    for said in ("october 24", "oct 24th", "oct. 24", "24th of october", "24 october"):
        assert mentions_date(f"that's {said}.", day), said
    for said in ("saturday", "the 24th", "october 25", "october 241", "october 24, 2027"):
        assert not mentions_date(f"that's {said}.", day), said
    assert mentions_date("that's october 24, 2026.", day)


# --- what was shared (PO-3) ----------------------------------------------------------------


def test_spoken_fields_match_names_as_whole_words() -> None:
    snapshot = {"calling_name": "Alex", "full_name": "Alex Chen", "email": "alex@example.test"}
    lines = ["Hi, I'm calling on behalf of Alex."]
    assert spoken_fields(snapshot, lines) == ["calling_name"]
    assert spoken_fields({"calling_name": "Al"}, ["We'd like it al dente."]) == []


def test_names_that_are_ordinary_words_need_a_name_cue() -> None:
    assert spoken_fields({"calling_name": "May"}, ["May I book a table?"]) == []
    assert spoken_fields({"calling_name": "May"}, ["Calling on behalf of May."]) == ["calling_name"]
    # "for" before a month and a day is a date, not the name
    assert spoken_fields({"calling_name": "May"}, ["a table for May 24"]) == []
    june = spoken_fields({"calling_name": "June"}, ["A table for June, please."])
    assert june == ["calling_name"]


def test_spoken_fields_only_check_identifying_fields() -> None:
    snapshot = {"dietary_restrictions": "None", "party_size": "two"}
    assert spoken_fields(snapshot, ["None for us, a table for two."]) == []


def test_spoken_fields_match_phone_numbers_by_digits() -> None:
    snapshot = {"phone": "+16175550142"}
    assert spoken_fields(snapshot, ["The number is 617-555-0142."]) == ["phone"]
    assert spoken_fields(snapshot, ["The number is (617) 555 0142."]) == ["phone"]
    assert spoken_fields(snapshot, ["The number is 617-555-0199."]) == []
    assert spoken_fields(snapshot, ["Table 617, party of 5, room 550, code 142."]) == []


@pytest.mark.parametrize(
    "said",
    ["April 12, 1990", "April 12th 1990", "12 April 1990", "04/12/1990", "4/12/90", "1990-04-12"],
)
def test_date_of_birth_is_found_in_common_spoken_forms(said: str) -> None:
    assert spoken_fields({"date_of_birth": "1990-04-12"}, [f"Her date of birth is {said}."]) == [
        "date_of_birth"
    ]


def test_date_of_birth_needs_the_year_and_the_right_day() -> None:
    snapshot = {"date_of_birth": "1990-04-12"}
    assert spoken_fields(snapshot, ["The appointment is April 12."]) == []
    assert spoken_fields(snapshot, ["Born 04/13/1990."]) == []
    # the year must go with the date, not appear somewhere else in the call
    assert spoken_fields(snapshot, ["The appointment is April 12.", "Ref 1990."]) == []


def test_address_matches_street_and_st_alike() -> None:
    snapshot = {"address": "12 Main St, Boston, MA"}
    assert spoken_fields(snapshot, ["The address is 12 Main Street, Boston."]) == ["address"]
    assert spoken_fields(snapshot, ["We're near Main Street."]) == []


def test_spoken_fields_skip_tiny_values_and_empty_transcripts() -> None:
    assert spoken_fields({"calling_name": "A"}, ["Calling for A."]) == []
    assert spoken_fields({"calling_name": "Alex"}, []) == []
