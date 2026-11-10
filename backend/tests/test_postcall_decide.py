"""Pure post-call rules (app/postcall/decide.py): no database needed."""

from typing import Any

from app.contracts.generated import ReportOutcomeArgs
from app.postcall.decide import (
    date_phrases,
    decide_outcome,
    has_confirmation_evidence,
    spoken_fields,
    time_phrases,
)


def report(**fields: Any) -> ReportOutcomeArgs:
    return ReportOutcomeArgs.model_validate({"business_confirmed": True, **fields})


BOOKED = report(outcome="booked", details={"date": "2026-10-24", "time": "19:30"})
CONFIRMING = ["Got it, Alex, party of 2 at 7:30 Saturday. See you then."]


def test_confirmed_booking_found_in_the_transcript_is_booked() -> None:
    decision = decide_outcome(BOOKED, "assistant-ended-call", CONFIRMING)
    assert decision.outcome == "booked"


def test_booking_the_business_did_not_confirm_needs_follow_up() -> None:
    unconfirmed = report(outcome="booked", business_confirmed=False, details={"time": "19:30"})
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


def test_time_phrases_cover_12_and_24_hour_forms() -> None:
    assert time_phrases("19:30") == ["7:30", "19:30"]
    assert time_phrases("19:00") == ["7:00", "19:00", "7pm", "7 pm", "7 o'clock"]
    assert time_phrases("09:00") == ["9:00", "9:00", "9am", "9 am", "9 o'clock"]
    assert time_phrases("noon") == ["noon"]


def test_date_phrases_cover_weekday_month_and_ordinal() -> None:
    assert date_phrases("2026-10-24") == ["saturday", "october 24", "oct 24", "24th"]
    assert date_phrases("2026-11-01")[-1] == "1st"
    assert date_phrases("next friday") == ["next friday"]


def test_evidence_does_not_match_inside_a_longer_number() -> None:
    details = report(outcome="booked", details={"time": "7:30"}).details
    assert not has_confirmation_evidence(details, ["We open at 17:30."])
    assert has_confirmation_evidence(details, ["See you at 7:30."])


def test_evidence_ignores_case() -> None:
    details = report(outcome="booked", details={"confirmation_number": "tb-1234"}).details
    assert has_confirmation_evidence(details, ["Your number is TB-1234."])


def test_spoken_fields_match_names_as_whole_words() -> None:
    snapshot = {"calling_name": "Alex", "full_name": "Alex Chen", "email": "alex@example.test"}
    lines = ["Hi, I'm calling on behalf of Alex."]
    assert spoken_fields(snapshot, lines) == ["calling_name"]
    assert spoken_fields({"calling_name": "Al"}, lines) == []


def test_spoken_fields_match_phone_numbers_by_digits() -> None:
    snapshot = {"phone": "+16175550142"}
    assert spoken_fields(snapshot, ["The number is 617-555-0142."]) == ["phone"]
    assert spoken_fields(snapshot, ["The number is 617-555-0199."]) == []


def test_spoken_fields_skip_tiny_values_and_empty_transcripts() -> None:
    assert spoken_fields({"party_size": "2"}, ["A table for 2."]) == []
    assert spoken_fields({"calling_name": "Alex"}, []) == []
