"""Privacy guard for everything that reaches the in-call model (ON-5, DC-1, DC-6).

Ask-me-first profile values must never appear in an assistant config. Three layers:
the brief holds no values, ``resolve_task_vars`` refuses ask-first ``field_ref`` fields, and
``assert_prompt_safe`` catches an ask-first value typed in as a literal.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

Sharing = Literal["share_freely", "ask_first"]

CALLING_NAME_KEY = "calling_name"
_NON_WORD = re.compile(r"[\W_]+")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
FIRST_MESSAGE_TEMPLATE = (
    "Hi, I'm an AI assistant calling on behalf of {calling_name}. "
    "This call is transcribed. {task_line}"
)


class AskFirstInPrompt(ValueError):
    """An ask-first field would reach the in-call model. Carries the key, never the value."""

    def __init__(self, key: str) -> None:
        super().__init__(f"ask-first field {key!r} must not appear in the assistant config")
        self.key = key


class MissingVar(ValueError):
    """A variable the brief needs has no value. This blocks Start (DC-1)."""

    def __init__(self, key: str) -> None:
        super().__init__(f"missing variable {key!r}")
        self.key = key


@dataclass(frozen=True)
class Snapshot:
    """Read-only profile snapshot: key -> (value, sharing), as stored in call_profile_snapshots."""

    fields: Mapping[str, tuple[str, Sharing]]

    @classmethod
    def from_rows(cls, rows: Iterable[tuple[str, str, Sharing]]) -> "Snapshot":
        """Build from ``(key, value, sharing)`` rows."""
        return cls({key: (value, sharing) for key, value, sharing in rows})

    def get(self, key: str) -> tuple[str, Sharing] | None:
        """Return ``(value, sharing)`` for ``key`` or None."""
        return self.fields.get(key)

    def items(self) -> Iterable[tuple[str, tuple[str, Sharing]]]:
        """Iterate ``(key, (value, sharing))``."""
        return self.fields.items()


def _tokens(text: str) -> list[str]:
    return [t for t in _NON_WORD.split(text.casefold()) if t]


def _contains_sequence(haystack: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return any(haystack[i : i + n] == needle for i in range(len(haystack) - n + 1))


def resolve_task_vars(brief: Mapping[str, Any], snapshot: Snapshot) -> dict[str, str]:
    """Resolve each task variable: literals as given, field_refs from the snapshot."""
    resolved: dict[str, str] = {}
    for name, spec in brief.get("task_vars", {}).items():
        if "literal" in spec:
            resolved[name] = str(spec["literal"])
            continue
        key = spec["field_ref"]
        entry = snapshot.get(key)
        if entry is None:
            raise MissingVar(key)
        value, sharing = entry
        if sharing == "ask_first":
            raise AskFirstInPrompt(key)
        resolved[name] = value
    return resolved


def assert_prompt_safe(text: str, snapshot: Snapshot) -> None:
    """Raise AskFirstInPrompt when an ask-first value appears in ``text`` as a token sequence."""
    text_tokens = _tokens(text)
    calling = snapshot.get(CALLING_NAME_KEY)
    calling_tokens = _tokens(calling[0]) if calling else None
    for key, (value, sharing) in snapshot.items():
        if sharing != "ask_first":
            continue
        value_tokens = _tokens(value)
        if value_tokens == calling_tokens and value_tokens:
            continue
        if value_tokens:
            if _contains_sequence(text_tokens, value_tokens):
                raise AskFirstInPrompt(key)
        elif value.strip() and value.casefold().strip() in text.casefold():
            # No word characters to tokenise: fail closed on a plain substring match.
            raise AskFirstInPrompt(key)


def build_first_message(brief: Mapping[str, Any], snapshot: Snapshot) -> str:
    """Build the DC-1 opening line; only ``calling_name`` identifies the user."""
    calling = snapshot.get(CALLING_NAME_KEY)
    if calling is None:
        raise MissingVar(CALLING_NAME_KEY)
    variables = resolve_task_vars(brief, snapshot)

    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise MissingVar(name)
        return variables[name]

    task_line = _PLACEHOLDER.sub(fill, str(brief["task_line"]))
    return FIRST_MESSAGE_TEMPLATE.format(calling_name=calling[0], task_line=task_line)
