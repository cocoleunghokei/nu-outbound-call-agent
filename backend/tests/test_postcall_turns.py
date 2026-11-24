"""The ordered transcript the outcome job reads from the end-of-call report. No database."""

from typing import Any

from app.postcall.handlers import report_lines, report_turns
from app.postcall.jobs import payload_turns

MESSAGE: dict[str, Any] = {
    "artifact": {
        "messages": [
            {"role": "system", "message": "You are an AI assistant."},
            {"role": "bot", "message": " Would 7:30 work instead? "},
            {"role": "user", "message": "Yeah. Sure."},
            {"role": "tool_calls", "message": "ignored"},
            {"role": "bot", "message": "   "},
            {"role": "bot", "message": 42},
            "not a message",
            {"role": "bot", "message": "Great. 7:30 PM then."},
        ]
    }
}


def test_the_report_keeps_the_order_of_the_call() -> None:
    assert report_turns(MESSAGE) == [
        ["agent", "Would 7:30 work instead?"],
        ["business", "Yeah. Sure."],
        ["agent", "Great. 7:30 PM then."],
    ]


def test_the_turns_agree_with_the_lines_split_by_speaker() -> None:
    lines = report_lines(MESSAGE)
    turns = report_turns(MESSAGE)
    assert lines["agent_lines"] == [text for role, text in turns if role == "agent"]
    assert lines["business_lines"] == [text for role, text in turns if role == "business"]


def test_a_report_with_no_messages_has_no_turns() -> None:
    assert report_turns({}) == []
    assert report_turns({"artifact": {"messages": "oops"}}) == []
    assert report_turns({"artifact": "oops"}) == []


def test_the_job_reads_the_turns_back_and_skips_malformed_ones() -> None:
    payload = {
        "turns": [
            ["agent", "Would 7:30 work?"],
            ["business", "Yes."],
            ["system", "dropped: not a speaker"],
            ["agent"],
            ["agent", 7],
            ["agent", "  "],
            "oops",
        ]
    }
    assert payload_turns(payload) == [("agent", "Would 7:30 work?"), ("business", "Yes.")]
    assert payload_turns({}) == []
    assert payload_turns({"turns": "oops"}) == []
