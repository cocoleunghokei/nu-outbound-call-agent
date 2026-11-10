"""Call templates (PC-2): load ``templates/templates.json`` and check every rule at import.

Templates are config, not code. The file has the shape agreed in plan Step 7 (one list of
``CallTemplate`` objects, the same file the chat will import). Importing this module loads and
checks it, so a bad template stops ``make build`` and the app's startup instead of failing a
call later. The rules below are R1 to R10 from the Step 7 proposal; R11 (no personal data) is a
review rule that code can't check.

Only the P0 templates are in the list. A template that isn't ready simply isn't in the file
(no ``enabled`` flag), and a retired key is never reused (R1).

The JSON Schema for this shape goes into ``contracts/`` later, with Steve's review; until then
the pydantic models here are the shape.
"""

import json
import re
from collections.abc import Sequence
from importlib import resources
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app.voice.prompt_safety import CALLING_NAME_KEY

KEY_PATTERN = r"^[a-z][a-z0-9_]*$"
_KEY = re.compile(KEY_PATTERN)
_PLACEHOLDER = re.compile(r"\{(\w+)\}")

TASK_LINE_MAX = 120  # brief.v1 task_line maxLength (R3)
LABEL_MAX = 40  # R2: chip text and the brief goal
ANSWER_MAX = 200  # chat.ts ANSWER_MAX: one-tap answers are answers (R9)
CONSTRAINT_MAX = 200  # chat.ts LIST_ITEM_MAX (R10)
DEFAULT_CONSTRAINTS_MAX = 3  # R10: leave room in the chat's 5-item list for the user's own
SUGGESTIONS_MAX = 5

# R7: the profile keys a template may ask for. Official list still to confirm with Steve;
# the insurance key names come from frontend missing.ts (ON-5 defaults).
PROFILE_KEYS = frozenset(
    {
        "full_name",
        CALLING_NAME_KEY,
        "phone",
        "email",
        "date_of_birth",
        "address",
        "insurance_carrier",
        "insurance_member_id",
    }
)

NonEmpty = Annotated[str, StringConstraints(min_length=1, strip_whitespace=False)]


class TemplateError(ValueError):
    """A template breaks the agreed format. The message names the template and the rule."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TemplateField(_Strict):
    """A piece of info a template needs, and how the chat asks for it."""

    key: NonEmpty
    source: Literal["profile", "call"]
    question: NonEmpty
    suggestions: tuple[NonEmpty, ...] = ()


class TemplateConstraint(_Strict):
    """A pre-filled hard limit, in the brief.v1 ``{text}`` shape."""

    text: NonEmpty


class CallTemplate(_Strict):
    """One call template (PC-2), exactly as stored in ``templates.json``."""

    key: NonEmpty
    label: NonEmpty
    task_line: str
    required_fields: tuple[TemplateField, ...] = Field(min_length=1)
    likely_questions: tuple[NonEmpty, ...]
    default_constraints: tuple[TemplateConstraint, ...]

    def placeholders(self) -> list[str]:
        """The ``{placeholder}`` names in ``task_line``, first occurrence order, no repeats."""
        return list(dict.fromkeys(_PLACEHOLDER.findall(self.task_line)))

    def profile_keys(self) -> list[str]:
        """Keys of the required fields that come from the profile, in template order."""
        return [f.key for f in self.required_fields if f.source == "profile"]

    def as_json(self) -> dict[str, Any]:
        """The template as the wire/JSON shape (``suggestions`` left out when empty)."""
        data: dict[str, Any] = self.model_dump(mode="json")
        for field in data["required_fields"]:
            if not field["suggestions"]:
                del field["suggestions"]
        return data


def _check_rules(template: CallTemplate) -> None:
    """R1 to R10 for one template. Raises TemplateError naming the first broken rule."""
    name = template.key

    def fail(rule: str, message: str) -> TemplateError:
        return TemplateError(f"template {name!r}: {rule}: {message}")

    if not _KEY.match(template.key):
        raise fail("R1", f"key must match {KEY_PATTERN}")
    if len(template.label.strip()) == 0 or len(template.label) > LABEL_MAX:
        raise fail("R2", f"label must be 1 to {LABEL_MAX} characters")
    if len(template.task_line) > TASK_LINE_MAX:
        raise fail("R3", f"task_line is over {TASK_LINE_MAX} characters")
    if not template.task_line.strip():
        raise fail("R3", "task_line is empty")

    keys = [f.key for f in template.required_fields]
    for field in template.required_fields:
        if not _KEY.match(field.key):
            raise fail("R8", f"field key {field.key!r} must match {KEY_PATTERN}")
    if len(set(keys)) != len(keys):
        raise fail("R8", "required field keys must be unique")

    by_key = {f.key: f for f in template.required_fields}
    for placeholder in template.placeholders():
        needed = by_key.get(placeholder)
        if needed is None:
            raise fail("R4", f"placeholder {{{placeholder}}} is not a required field key")
        if needed.source == "profile" and placeholder != CALLING_NAME_KEY:
            raise fail(
                "R5",
                f"placeholder {{{placeholder}}} is a profile field; only calling_name may be one",
            )

    for field in template.required_fields:
        if field.source == "profile" and field.key not in PROFILE_KEYS:
            raise fail("R7", f"{field.key!r} is not a known profile key")
        if field.source == "call" and field.key in PROFILE_KEYS:
            raise fail("R8", f"call field {field.key!r} clashes with a profile key")
        question = field.question.strip()
        if not question.endswith("?") or question.count("?") != 1:
            raise fail("R9", f"question for {field.key!r} must be one question ending in '?'")
        if len(field.suggestions) > SUGGESTIONS_MAX:
            raise fail("R9", f"at most {SUGGESTIONS_MAX} suggestions for {field.key!r}")
        for suggestion in field.suggestions:
            if not suggestion.strip() or len(suggestion) > ANSWER_MAX:
                raise fail("R9", f"suggestions for {field.key!r} must be 1 to {ANSWER_MAX} chars")

    if len(template.default_constraints) > DEFAULT_CONSTRAINTS_MAX:
        raise fail("R10", f"at most {DEFAULT_CONSTRAINTS_MAX} default_constraints")
    for constraint in template.default_constraints:
        if not constraint.text.strip() or len(constraint.text) > CONSTRAINT_MAX:
            raise fail("R10", f"default_constraints text must be 1 to {CONSTRAINT_MAX} chars")


def parse_templates(data: object) -> tuple[CallTemplate, ...]:
    """Parse and check a template list (the decoded ``templates.json``).

    Raises TemplateError for a wrong shape (unknown or missing keys, wrong types) or a broken
    rule. Keys must be unique across the list (R1).
    """
    if not isinstance(data, list):
        raise TemplateError("templates.json must hold a list of templates")
    templates: list[CallTemplate] = []
    for index, item in enumerate(data):
        try:
            template = CallTemplate.model_validate(item)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            raise TemplateError(f"template #{index}: {where}: {first['msg']}") from exc
        _check_rules(template)
        templates.append(template)
    keys = [t.key for t in templates]
    if len(set(keys)) != len(keys):
        raise TemplateError("R1: template keys must be unique")
    return tuple(templates)


def load_templates() -> tuple[CallTemplate, ...]:
    """Read ``templates/templates.json`` from this package and check it."""
    text = (
        resources.files(__package__ or "app.briefs") / "templates" / "templates.json"
    ).read_text(encoding="utf-8")
    return parse_templates(json.loads(text))


TEMPLATES: tuple[CallTemplate, ...] = load_templates()
_BY_KEY = {t.key: t for t in TEMPLATES}


def get_template(key: str) -> CallTemplate | None:
    """The template with this key, or None."""
    return _BY_KEY.get(key)


def templates_json(templates: Sequence[CallTemplate] = TEMPLATES) -> list[dict[str, Any]]:
    """Every template in list order, in the JSON shape the chat reads."""
    return [t.as_json() for t in templates]
