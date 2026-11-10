"""The wording the backend puts into a live call (no database)."""

import pytest

from app.calls import messages


def test_sanitize_reply_text_strips_control_characters_and_whitespace() -> None:
    assert messages.sanitize_reply_text("  ask\tfor\n Saturday\u0007 ") == "ask for Saturday"
    assert messages.sanitize_reply_text("ok\x00\x9bgo") == "ok go"


def test_sanitize_reply_text_caps_the_length() -> None:
    assert len(messages.sanitize_reply_text("a" * 500)) == messages.MAX_REPLY_LENGTH


def test_sanitize_reply_text_can_be_empty() -> None:
    assert messages.sanitize_reply_text(" \n\x01 ") == ""


@pytest.mark.parametrize(
    ("key", "label"),
    [
        ("date_of_birth", "date of birth"),
        ("insurance_member_id", "insurance member id"),
        ("_", "detail"),
    ],
)
def test_field_label(key: str, label: str) -> None:
    assert messages.field_label(key) == label


def test_field_approval_question_never_contains_a_value() -> None:
    question, reason = messages.field_approval_question("date_of_birth")
    assert question == "They're asking for your date of birth"
    assert reason == "You set date of birth to Ask me first."


def test_answer_quotes_the_question_it_answers() -> None:
    text = messages.answer_message("Alex", "They offered Thu 4pm", "Ask for Fri")
    assert text == 'Alex\'s answer to "They offered Thu 4pm": Ask for Fri'


def test_declined_field_message_has_no_value() -> None:
    text = messages.field_declined_message("Alex", "Q", "date of birth")
    assert "do not share" in text


def test_control_message_shapes() -> None:
    assert messages.say("hi") == {"type": "say", "content": "hi"}
    assert messages.add_message("x") == {
        "type": "add-message",
        "message": {"role": "system", "content": "x"},
        "triggerResponseEnabled": True,
    }
