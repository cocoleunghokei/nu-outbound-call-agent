"""The requirement list anchors the date (``app/calls/requested_day.py``, ``report_day_gate``).

A user typed "Dec 23"; the call was transcribed "4 on deck 23" and the agent reported
2026-10-23. Pure tests, fake data.
"""

from datetime import date
from typing import Any

import pytest

from app.calls.gates import CallFacts, report_day_gate, wrong_day_refusal
from app.calls.prompt import build_system_prompt
from app.calls.requested_day import (
    business_named_day,
    parse_requested_day,
    requested_day,
    rule_days,
)
from app.contracts.generated import Brief
from app.voice.prompt_safety import Snapshot

NOV_24 = date(2026, 11, 24)
DEC_23 = date(2026, 12, 23)
SNAPSHOT = Snapshot.from_rows([("calling_name", "Alex", "share_freely")])


@pytest.mark.parametrize(
    "typed",
    ["Dec 23", "dec. 23", "December 23rd", "23rd of December", "23 Dec", "12/23", "12/23/26"]
    + ["Dec 23, 2026", "party of 2 on Dec 23", "Wednesday, December 23"],
)
def test_a_typed_day_is_read_into_a_date(typed: str) -> None:
    assert parse_requested_day(typed, NOV_24) == DEC_23


def test_a_day_without_a_year_is_the_next_one() -> None:
    assert parse_requested_day("Dec 23", date(2026, 12, 23)) == DEC_23  # today counts
    assert parse_requested_day("Dec 23", date(2026, 12, 24)) == date(2027, 12, 23)
    assert parse_requested_day("Nov 23", NOV_24) == date(2027, 11, 23)
    assert parse_requested_day("Dec 23 2027", NOV_24) == date(2027, 12, 23)


@pytest.mark.parametrize(
    "typed",
    ["Friday", "tomorrow", "next week", "the 23rd", "", "Feb 30", "Dec 23 or Jan 2", "deck 23"],
)
def test_anything_unclear_anchors_nothing(typed: str) -> None:
    assert parse_requested_day(typed, NOV_24) is None


def brief_with(**overrides: Any) -> dict[str, Any]:
    brief: dict[str, Any] = {"task_vars": {"day": {"literal": "Dec 23"}}}
    brief.update(overrides)
    return brief


def test_the_day_comes_from_the_briefs_day_answer() -> None:
    assert requested_day(brief_with(), NOV_24) == DEC_23
    assert requested_day({"task_vars": {"date": {"literal": "Dec 23"}}}, NOV_24) == DEC_23
    assert requested_day({"task_vars": {"day": {"field_ref": "day"}}}, NOV_24) is None
    assert requested_day({"task_vars": {"time": {"literal": "Dec 23"}}}, NOV_24) is None
    assert requested_day({"task_vars": "oops"}, NOV_24) is None
    assert requested_day(None, NOV_24) is None


def test_date_rules_list_every_allowed_day() -> None:
    brief = {
        "constraints": [{"text": "x", "rules": [{"kind": "date", "on": "2026-12-23"}]}],
        "preferences": [
            {
                "text": "y",
                "rules": [{"kind": "date", "earliest": "2026-12-26", "latest": "2026-12-28"}],
            }
        ],
        "fallbacks": [{"text": "z", "rules": [{"kind": "time", "earliest": "19:00"}]}, "oops"],
    }
    assert rule_days(brief) == {
        date(2026, 12, 23),
        date(2026, 12, 26),
        date(2026, 12, 27),
        date(2026, 12, 28),
    }
    assert rule_days(None) == set()
    assert rule_days({"constraints": [{"rules": [{"kind": "date", "on": "2026-02-30"}]}]}) == set()


def test_a_day_the_business_named_loosely_counts() -> None:
    assert business_named_day("how about december 24?", date(2026, 12, 24))
    assert business_named_day("we have the 24th open", date(2026, 12, 24))
    assert business_named_day("what about thursday", date(2026, 12, 24))
    assert business_named_day("24 dec works", date(2026, 12, 24))
    assert not business_named_day("we don't have 7 pm. does 8 work for you?", DEC_23)
    assert not business_named_day("yeah. sure.", date(2026, 10, 23))


# --- the gate on report_outcome ---------------------------------------------------------


def report(day: str | None) -> dict[str, Any]:
    details = {"time": "19:30"} | ({"date": day} if day else {})
    return {"outcome": "booked", "business_confirmed": True, "details": details}


SILENT = CallFacts(business_said="we don't have 7 pm. does 8 work for you? yeah. sure.")


def test_the_reported_call_is_refused_and_told_the_right_day() -> None:
    refusal = report_day_gate(report("2026-10-23"), DEC_23, set(), SILENT)
    assert refusal == wrong_day_refusal(date(2026, 10, 23), DEC_23)
    assert refusal is not None
    assert refusal.startswith("Not done:")
    assert "Wednesday 23 December 2026 (2026-12-23)" in refusal
    assert "Call report_outcome again with date 2026-12-23" in refusal


def test_the_day_the_user_asked_for_passes() -> None:
    assert report_day_gate(report("2026-12-23"), DEC_23, set(), SILENT) is None


def test_a_day_the_briefs_rules_allow_passes() -> None:
    assert report_day_gate(report("2026-12-26"), DEC_23, {date(2026, 12, 26)}, SILENT) is None


def test_a_different_day_the_business_offered_passes() -> None:
    offered = CallFacts(business_said="we're full on the 23rd. how about december 24?")
    assert report_day_gate(report("2026-12-24"), DEC_23, set(), offered) is None


def test_nothing_is_checked_without_a_requested_day_or_a_valid_date() -> None:
    assert report_day_gate(report("2026-10-23"), None, set(), SILENT) is None
    assert report_day_gate(report(None), DEC_23, set(), SILENT) is None
    assert report_day_gate(report("2026-02-30"), DEC_23, set(), SILENT) is None
    assert report_day_gate({"outcome": "booked"}, DEC_23, set(), SILENT) is None
    assert report_day_gate({"details": "oops"}, DEC_23, set(), SILENT) is None


# --- the prompt -------------------------------------------------------------------------


def brief_model(day: str) -> Brief:
    return Brief.model_validate(
        {
            "goal": "Book a table",
            "business_name": "Coco Resto",
            "phone_e164": "+16175550100",
            "template_key": "restaurant_booking",
            "task_line": "I'd like to book a table for {party_size} on {day} at {time}.",
            "task_vars": {
                "party_size": {"literal": "4"},
                "day": {"literal": day},
                "time": {"literal": "7 PM"},
            },
            "constraints": [],
            "preferences": [],
            "fallbacks": [],
            "never_agree": [],
            "share_freely_keys": [],
            "ask_first_keys": [],
        }
    )


def test_the_prompt_names_the_day_the_user_asked_for_with_its_year() -> None:
    prompt = build_system_prompt(brief_model("Dec 23"), SNAPSHOT, NOV_24)
    assert (
        "<dates>\n- Wednesday 23 December 2026 (2026-12-23) - the day asked for\n</dates>" in prompt
    )
    assert "the day asked for" in prompt.replace("\n", " ")
    assert "deck 23" in prompt.replace("\n", " ")  # the guidance says why it stays Dec 23


def test_the_prompt_has_no_asked_for_day_without_today_or_a_readable_day() -> None:
    assert "<dates>\n- (none)\n</dates>" in build_system_prompt(brief_model("Dec 23"), SNAPSHOT)
    assert "<dates>\n- (none)\n</dates>" in build_system_prompt(
        brief_model("Friday"), SNAPSHOT, NOV_24
    )
