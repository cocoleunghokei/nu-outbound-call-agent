"""Pure post-call rules (app/postcall/decide.py): no database needed."""

from typing import Any

import pytest

from app.contracts.generated import ReportOutcomeArgs
from app.postcall.decide import (
    decide_outcome,
    has_confirmation_evidence,
    is_refusal,
    mentions_date,
    mentions_time,
    parse_time,
    spoken_fields,
)


def report(**fields: Any) -> ReportOutcomeArgs:
    return ReportOutcomeArgs.model_validate({"business_confirmed": True, **fields})


BOOKED = report(outcome="booked", details={"date": "2026-10-24", "time": "19:30"})
CONFIRMING = ["Got it, Alex, party of 2 at 7:30 on Saturday, October 24th. See you then."]


def test_confirmed_booking_found_in_the_transcript_is_booked() -> None:
    decision = decide_outcome(BOOKED, "assistant-ended-call", CONFIRMING)
    assert decision.outcome == "booked"


def test_business_refusing_the_slot_is_never_booked() -> None:
    # The review's case: time and weekday appear, but the business is refusing.
    for line in (
        "Sorry, Saturday at 7:30 is full.",
        "We're fully booked October 24 at 7:30, sorry.",
        "We can't do October 24th at 7:30.",
        "There's no availability on Oct 24 at 7:30pm.",
    ):
        assert decide_outcome(BOOKED, "", [line]).outcome == "needs_follow_up", line


def test_refusal_lines_are_ignored_but_a_later_confirmation_counts() -> None:
    lines = ["Sorry, 7 is full.", "But 7:30 on October 24 works, you're booked."]
    assert decide_outcome(BOOKED, "", lines).outcome == "booked"


def test_a_bare_weekday_or_ordinal_is_not_a_date() -> None:
    for line in ("Got it, party of 2 at 7:30 Saturday.", "See you at 7:30 on the 24th."):
        assert decide_outcome(BOOKED, "", [line]).outcome == "needs_follow_up", line


def test_time_alone_or_date_alone_is_not_enough() -> None:
    assert decide_outcome(BOOKED, "", ["Yes, 7:30 works."]).outcome == "needs_follow_up"
    assert decide_outcome(BOOKED, "", ["Yes, October 24 works."]).outcome == "needs_follow_up"


def test_booking_the_business_did_not_confirm_needs_follow_up() -> None:
    unconfirmed = report(
        outcome="booked", business_confirmed=False, details={"date": "2026-10-24", "time": "19:30"}
    )
    assert decide_outcome(unconfirmed, "", CONFIRMING).outcome == "needs_follow_up"


def test_booking_with_no_matching_words_from_the_business_needs_follow_up() -> None:
    decision = decide_outcome(BOOKED, "", ["Let me check and call you back."])
    assert decision.outcome == "needs_follow_up"
    assert "transcript" in decision.reason


def test_booking_with_no_details_needs_follow_up() -> None:
    assert decide_outcome(report(outcome="booked"), "", CONFIRMING).outcome == "needs_follow_up"


def test_cancel_confirmed_by_number_is_cancelled() -> None:
    cancelled = report(outcome="cancelled", details={"confirmation_number": "EP-5521"})
    lines = ["Got it, that's cancelled. Your confirmation is EP-5521."]
    assert decide_outcome(cancelled, "", lines).outcome == "cancelled"


def test_outcomes_that_arrange_nothing_are_taken_as_reported() -> None:
    for outcome in ("refused_ai", "wrong_number", "needs_follow_up"):
        assert decide_outcome(report(outcome=outcome), "", []).outcome == outcome


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
    ],
)
def test_parse_time(value: str, expected: tuple[int, int] | None) -> None:
    assert parse_time(value) == expected


def test_mentions_time_accepts_am_pm_and_24_hour_forms() -> None:
    for said in ("7:30", "7:30pm", "7:30 pm", "19:30"):
        assert mentions_time(f"see you at {said}.", "19:30"), said
    assert mentions_time("see you at 7pm.", "19:00")
    assert mentions_time("see you at 7 p.m.", "7 PM")
    assert mentions_time("see you at 7 o'clock.", "19:00")


def test_mentions_time_does_not_match_inside_a_longer_number_or_the_wrong_half_of_day() -> None:
    assert not mentions_time("we open at 17:30.", "7:30")
    assert not mentions_time("see you at 7am.", "19:00")


def test_mentions_date_needs_month_and_day() -> None:
    for said in ("october 24", "oct 24th", "oct. 24", "24th of october", "24 october"):
        assert mentions_date(f"that's {said}.", "2026-10-24"), said
    for said in ("saturday", "the 24th", "october 25", "october 241"):
        assert not mentions_date(f"that's {said}.", "2026-10-24"), said


def test_evidence_ignores_case() -> None:
    details = report(outcome="booked", details={"confirmation_number": "tb-1234"}).details
    assert has_confirmation_evidence(details, ["Your number is TB-1234."])


def test_is_refusal() -> None:
    assert is_refusal("Sorry, that's full")
    assert is_refusal("We can’t do 7:30")
    assert not is_refusal("Got it, see you at 7:30")


def test_spoken_fields_match_names_as_whole_words() -> None:
    snapshot = {"calling_name": "Alex", "full_name": "Alex Chen", "email": "alex@example.test"}
    lines = ["Hi, I'm calling on behalf of Alex."]
    assert spoken_fields(snapshot, lines) == ["calling_name"]
    assert spoken_fields({"calling_name": "Al"}, lines) == []


def test_spoken_fields_only_check_identifying_fields() -> None:
    # dietary_restrictions="None" must not count when the agent says "none".
    snapshot = {"dietary_restrictions": "None", "party_size": "two"}
    assert spoken_fields(snapshot, ["None for us, a table for two."]) == []


def test_spoken_fields_match_phone_numbers_by_digits() -> None:
    snapshot = {"phone": "+16175550142"}
    assert spoken_fields(snapshot, ["The number is 617-555-0142."]) == ["phone"]
    assert spoken_fields(snapshot, ["The number is (617) 555 0142."]) == ["phone"]
    assert spoken_fields(snapshot, ["The number is 617-555-0199."]) == []


def test_spoken_fields_do_not_join_digits_across_separate_numbers() -> None:
    snapshot = {"phone": "+16175550142"}
    assert spoken_fields(snapshot, ["Table 617, party of 5, room 550, code 142."]) == []


def test_spoken_fields_skip_tiny_values_and_empty_transcripts() -> None:
    assert spoken_fields({"calling_name": "A"}, ["Calling for A."]) == []
    assert spoken_fields({"calling_name": "Alex"}, []) == []
