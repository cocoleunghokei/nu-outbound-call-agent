"""Is a brief ready to dial? (PC-4, PC-6, DC-1)

Collects every gap at once (the review card lists them all) and uses the voice package's
``build_first_message`` and ``assert_prompt_safe`` as the final word, read-only. A brief is
ready when ``missing_fields`` is empty; then ``opening_line`` is exactly what the agent
will say first.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.voice.prompt_safety import (
    CALLING_NAME_KEY,
    AskFirstInPrompt,
    MissingVar,
    Snapshot,
    assert_prompt_safe,
    build_first_message,
)

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class Readiness:
    """``missing_fields`` (in order, no repeats) and the opening line when nothing is missing."""

    missing_fields: list[str] = field(default_factory=list)
    opening_line: str | None = None

    @property
    def ready(self) -> bool:
        """True when Start may proceed."""
        return not self.missing_fields and self.opening_line is not None


def _add(missing: list[str], key: str) -> None:
    if key not in missing:
        missing.append(key)


def check_readiness(brief: Mapping[str, Any], snapshot: Snapshot) -> Readiness:
    """Check the brief against the user's profile snapshot.

    Gaps reported, by field key:
    - ``calling_name`` when the profile has none (the opening line needs it, ON-3).
    - A ``{placeholder}`` in ``task_line`` with no ``task_vars`` entry.
    - A ``field_ref`` to a profile field that is empty, or is Ask me first (never in a prompt).
    - An Ask-me-first value typed into the brief as text (caught by ``assert_prompt_safe``).
    An empty ``fallbacks`` list is never a gap: PC-5 only prompts for one.
    """
    missing: list[str] = []
    if snapshot.get(CALLING_NAME_KEY) is None:
        _add(missing, CALLING_NAME_KEY)

    task_vars: Mapping[str, Mapping[str, str]] = brief.get("task_vars", {})
    for name in _PLACEHOLDER.findall(str(brief.get("task_line", ""))):
        if name not in task_vars:
            _add(missing, name)
    for spec in task_vars.values():
        ref = spec.get("field_ref")
        if ref is None:
            continue
        entry = snapshot.get(ref)
        if entry is None or entry[1] == "ask_first":
            _add(missing, ref)
    if missing:
        return Readiness(missing_fields=missing)

    try:
        opening = build_first_message(brief, snapshot)
        # The whole brief, not just the opening line, must be free of ask-first values.
        assert_prompt_safe(_brief_text(brief), snapshot)
    except (MissingVar, AskFirstInPrompt) as exc:
        key = getattr(exc, "key", None) or str(exc)
        return Readiness(missing_fields=[key])
    return Readiness(missing_fields=[], opening_line=opening)


def _brief_text(brief: Mapping[str, Any]) -> str:
    """Every free-text string in the brief, joined, for the ask-first value scan."""
    parts: list[str] = [str(brief.get("goal", "")), str(brief.get("task_line", ""))]
    for spec in brief.get("task_vars", {}).values():
        if "literal" in spec:
            parts.append(str(spec["literal"]))
    for list_name in ("constraints", "preferences", "fallbacks", "never_agree"):
        parts.extend(str(item.get("text", "")) for item in brief.get(list_name, []))
    return "\n".join(parts)
