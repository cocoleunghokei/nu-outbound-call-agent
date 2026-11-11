"""Every sentence the backend puts into a live call, and the Vapi control messages that carry it.

Kept in one place so the wording can be reviewed and tuned from test calls without touching
the state logic. Nothing here talks to the database or the network.
"""

import re
from typing import Any

# Free-text replies are capped at this many characters (DC-8, ping-reply.v1.json).
MAX_REPLY_LENGTH = 200

# When the calling name is unknown, the agent still needs someone to refer to.
DEFAULT_CALLING_NAME = "the person I'm calling for"

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_WHITESPACE = re.compile(r"\s+")

# Order matters: a field_approval reply's `choice` indexes this list.
FIELD_APPROVAL_REPLIES: tuple[str, str] = ("Share it", "Don't share")
SHARE_CHOICE = 0


def sanitize_reply_text(raw: str) -> str:
    """Clean a free-text reply (DC-8): strip control characters, collapse whitespace, trim, cap.

    Returns an empty string when nothing is left. No LLM call: the in-call model reads it.
    """
    text = _WHITESPACE.sub(" ", _CONTROL_CHARS.sub(" ", raw)).strip()
    return text[:MAX_REPLY_LENGTH].strip()


def field_label(field_key: str) -> str:
    """A readable name for a profile field key, e.g. ``date_of_birth`` -> ``date of birth``."""
    return field_key.replace("_", " ").strip() or "detail"


def field_approval_question(field_key: str) -> tuple[str, str]:
    """The server-written question and reason for an Ask-me-first ping (DC-6)."""
    label = field_label(field_key)
    return f"They're asking for your {label}", f"You set {label} to Ask me first."


def ask_user_result(calling_name: str) -> str:
    """What the ``ask_user`` tool returns to the in-call model (it must answer within 500 ms)."""
    return (
        f"Asked {calling_name}. Tell the other side you are checking with {calling_name}, "
        "then wait quietly. Their answer will arrive as a system message that quotes your "
        "question. Do not guess in the meantime."
    )


def superseded_result() -> str:
    """Tool result when a question arrives that is older than one already asked (DC-7)."""
    return "A newer question is already with the user. Keep waiting for their answer."


def call_over_result() -> str:
    """Tool result when the call has already ended."""
    return "The call has ended. Do not ask anything else."


def answer_message(calling_name: str, question: str, reply_text: str) -> str:
    """The user's answer, quoting the question it answers so it can't be applied to another."""
    return f'{calling_name}\'s answer to "{question}": {reply_text}'


def field_shared_message(calling_name: str, question: str, label: str, value: str) -> str:
    """An approved Ask-me-first value. Only an explicit Share releases it, for this call only."""
    return (
        f'{calling_name}\'s answer to "{question}": share it. '
        f"Their {label} is {value}. Use it only for this request."
    )


def field_declined_message(calling_name: str, question: str, label: str) -> str:
    """The user chose not to share an Ask-me-first field."""
    return (
        f'{calling_name}\'s answer to "{question}": do not share it. '
        f"Say {calling_name} will provide their {label} later, and carry on without it."
    )


def timeout_message() -> str:
    """Sent when nobody replied before the ping expired (DC-9)."""
    return "The user did not reply in time. Use a matching fallback or end politely."


def filler_line(calling_name: str) -> str:
    """What the business hears every 15 s while a ping is open (DC-7)."""
    return f"Thanks for holding, I'm still checking with {calling_name}."


def add_message(content: str) -> dict[str, Any]:
    """A Vapi control message that adds a system message and lets the model respond to it."""
    return {
        "type": "add-message",
        "message": {"role": "system", "content": content},
        "triggerResponseEnabled": True,
    }


def say(content: str) -> dict[str, Any]:
    """A Vapi control message that speaks an exact sentence."""
    return {"type": "say", "content": content}


# --- Pause / Resume / End (DC-11) -----------------------------------------------------------


def hold_line() -> str:
    """What the business hears the moment the user taps Pause."""
    return "One moment please."


def reassurance_line(calling_name: str) -> str:
    """What the business hears every 15 s while the call is paused."""
    return f"Thanks for holding, I'm still checking with {calling_name}. Just a moment longer."


def goodbye_line(calling_name: str) -> str:
    """The polite goodbye when the user taps End call."""
    return (
        f"I'm sorry, I need to go now. {calling_name} will follow up. "
        "Thank you for your time. Goodbye."
    )


def pause_cap_goodbye_line(calling_name: str) -> str:
    """The call-back offer when a pause runs past the cap."""
    return (
        f"I'm sorry for the wait. {calling_name} will call you back. "
        "Thank you for your time. Goodbye."
    )


def late_adoption_apology_line() -> str:
    """What the business hears when a call we could not confirm turns up too late to use."""
    return "I'm sorry, this call was placed by mistake. Please disregard it. Goodbye."


def say_and_hang_up(content: str) -> dict[str, Any]:
    """Speak a sentence, then let Vapi end the call once it has been spoken."""
    return {"type": "say", "content": content, "endCallAfterSpoken": True}


def mute_assistant() -> dict[str, Any]:
    """Silence the assistant while the call is paused."""
    return {"type": "control", "control": "mute-assistant"}


def unmute_assistant() -> dict[str, Any]:
    """Let the assistant speak again."""
    return {"type": "control", "control": "unmute-assistant"}


def end_call() -> dict[str, Any]:
    """Hang up now: the fallback if the goodbye did not end the call."""
    return {"type": "end-call"}
