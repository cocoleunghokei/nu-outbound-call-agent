"""Brief builder (PC-3, PC-4): a finished chat draft + profile + template -> brief + gaps.

Pure functions that mirror ``frontend/src/precall/missing.ts`` and ``brief.ts`` rule for
rule, so the chat and the server give the same answers (plan Step 9). Step 10's LLM fills a
``Draft`` for custom goals and calls ``build_brief`` the same way.

Privacy rule (Steve, PR #19): the brief holds field keys, never profile values. A Share-freely
profile field is referenced with ``field_ref``; an Ask-me-first field is never referenced (the
agent pings before sharing it, DC-6) and only appears by key in ``ask_first_keys``. Only call
details the user typed for this call go in as ``literal``s.

One deliberate difference from the frontend: a profile addition can't loosen the sharing of
a field the profile already holds as Ask me first (see ``profile_with_additions``). The chat
never sends one, because it only asks for profile fields the profile lacks.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.briefs.catalog import CallTemplate, TemplateField
from app.voice.prompt_safety import CALLING_NAME_KEY, Sharing, Snapshot

# ON-5: these default to Ask me first; everything else to Share freely.
ASK_FIRST_BY_DEFAULT = frozenset(
    {"date_of_birth", "address", "insurance_carrier", "insurance_member_id"}
)

# Opening task line for a custom call (tech doc DC-1). The goal fills {goal_summary}.
CUSTOM_TASK_LINE = "I'm calling to {goal_summary}."
GOAL_SUMMARY_KEY = "goal_summary"
# Tech doc DC-1: the custom task line is at most 15 words, and the resolved line must fit
# brief.v1's 120 characters. A longer {goal_summary} is reported missing, so the chat asks
# the user to say it shorter instead of the agent opening with a paragraph.
GOAL_SUMMARY_MAX_WORDS = 15
GOAL_SUMMARY_MAX_CHARS = 120 - len(CUSTOM_TASK_LINE.replace("{goal_summary}", ""))


def goal_summary_fits(summary: str) -> bool:
    """True when ``summary`` makes a custom opening line within DC-1's limits."""
    text = summary.strip()
    return 0 < len(text) <= GOAL_SUMMARY_MAX_CHARS and len(text.split()) <= GOAL_SUMMARY_MAX_WORDS


# Every brief may share these: the calling name opens the call (DC-1), phone is the callback.
BASE_SHARED_KEYS: tuple[str, ...] = (CALLING_NAME_KEY, "phone")

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_TRAILING_PUNCTUATION = re.compile(r"[.!?]+$")
LIST_NAMES: tuple[str, ...] = ("constraints", "preferences", "fallbacks", "never_agree")


@dataclass(frozen=True)
class ProfileField:
    """One ``profile_fields`` row: key, value and sharing (ON-5)."""

    key: str
    value: str
    sharing: Sharing


@dataclass(frozen=True)
class Draft:
    """What the chat (or Step 10's LLM) collected for one call. Mirrors frontend ``CallDraft``.

    ``answers``: template details for this call only (source "call"), by field key.
    ``profile_additions``: profile fields the user typed in the chat because the profile
    lacked them, as ``(key, value)``. Used for this build only, with ON-5 default sharing.
    List items are plain strings (the brief.v1 ``{text}`` objects are made here).
    ``goal_summary``: custom calls only, the ``{goal_summary}`` the LLM wrote (Step 10). When
    None the goal itself is used (``goal_summary(goal)``). Template calls ignore it.
    """

    template_key: str | None
    goal: str
    business_name: str
    phone_e164: str
    answers: Mapping[str, str] = field(default_factory=dict)
    profile_additions: Sequence[tuple[str, str]] = ()
    constraints: Sequence[str] = ()
    preferences: Sequence[str] = ()
    fallbacks: Sequence[str] = ()
    never_agree: Sequence[str] = ()
    goal_summary: str | None = None


@dataclass(frozen=True)
class BuiltBrief:
    """A ``brief.v1`` brief and the field keys still missing (Start is blocked until empty)."""

    brief: dict[str, Any]
    missing_fields: list[str]


def default_sharing(key: str) -> Sharing:
    """ON-5 default sharing for a new profile field. The calling name is always shared."""
    if key == CALLING_NAME_KEY:
        return "share_freely"
    return "ask_first" if key in ASK_FIRST_BY_DEFAULT else "share_freely"


def has_profile_value(profile: Iterable[ProfileField], key: str) -> bool:
    """True when the profile has a non-blank value for ``key``."""
    return any(f.key == key and f.value.strip() != "" for f in profile)


def _has_answer(answers: Mapping[str, str], key: str) -> bool:
    return answers.get(key, "").strip() != ""


def missing_template_fields(
    template: CallTemplate, profile: Sequence[ProfileField], answers: Mapping[str, str]
) -> list[TemplateField]:
    """The template's required fields still missing, in template order (PC-4).

    A profile field is missing only when the profile has no value for it: an Ask-me-first
    field already in the profile is never asked again. A call field is missing until it has
    a non-blank answer.
    """
    return [
        f
        for f in template.required_fields
        if (
            not has_profile_value(profile, f.key)
            if f.source == "profile"
            else not _has_answer(answers, f.key)
        )
    ]


def known_profile_fields(template: CallTemplate, profile: Sequence[ProfileField]) -> list[str]:
    """The template's profile fields the profile already has (the chat won't ask for them)."""
    return [k for k in template.profile_keys() if has_profile_value(profile, k)]


def with_profile_field(profile: Sequence[ProfileField], key: str, value: str) -> list[ProfileField]:
    """Add or replace a profile field with its ON-5 default sharing, keeping the order.

    Replacing never loosens sharing: a field the profile holds as Ask me first stays so.
    """
    sharing = default_sharing(key)
    if any(f.key == key and f.sharing == "ask_first" for f in profile):
        sharing = "ask_first"
    new = ProfileField(key=key, value=value, sharing=sharing)
    if any(f.key == key for f in profile):
        return [new if f.key == key else f for f in profile]
    return [*profile, new]


def profile_with_additions(
    profile: Sequence[ProfileField], additions: Iterable[tuple[str, str]]
) -> list[ProfileField]:
    """The profile plus the fields typed in the chat. Nothing is written anywhere."""
    result = list(profile)
    for key, value in additions:
        result = with_profile_field(result, key, value)
    return result


def profile_from_snapshot(snapshot: Snapshot) -> list[ProfileField]:
    """The stored profile (``store.load_snapshot``) as a field list."""
    return [ProfileField(key, value, sharing) for key, (value, sharing) in snapshot.items()]


def snapshot_of(profile: Iterable[ProfileField]) -> Snapshot:
    """A readiness snapshot of the non-blank fields, as ``load_snapshot`` would build it."""
    return Snapshot.from_rows((f.key, f.value, f.sharing) for f in profile if f.value.strip())


def goal_summary(goal: str) -> str:
    """'Ask if they can hold a cake.' -> 'ask if they can hold a cake', to follow 'calling to'."""
    trimmed = _TRAILING_PUNCTUATION.sub("", goal.strip())
    return trimmed[:1].lower() + trimmed[1:]


def _placeholders(task_line: str) -> list[str]:
    return list(dict.fromkeys(_PLACEHOLDER.findall(task_line)))


def build_brief(
    draft: Draft, template: CallTemplate | None, profile: Sequence[ProfileField]
) -> BuiltBrief:
    """Build the ``brief.v1`` brief and ``missing_fields`` for a finished draft.

    - Template calls use the template's ``task_line``; custom calls use ``CUSTOM_TASK_LINE``
      with ``draft.goal_summary`` (the LLM's, Step 10) or else the goal as ``{goal_summary}``.
    - Each placeholder becomes a ``literal`` from the answers, else a ``field_ref`` to a
      Share-freely profile field, else it is missing (Ask-me-first is never referenced).
    - ``share_freely_keys`` / ``ask_first_keys``: calling_name, phone and the template's
      profile fields that the profile has, split by sharing.
    - ``missing_fields``: unresolved placeholders, then missing template fields, then
      ``calling_name`` if absent; no repeats. An empty ``fallbacks`` list is never missing.
    """
    full_profile = profile_with_additions(profile, draft.profile_additions)
    by_key = {f.key: f for f in full_profile if f.value.strip() != ""}
    missing: list[str] = []

    task_line = template.task_line if template else CUSTOM_TASK_LINE
    summary = draft.goal_summary or goal_summary(draft.goal)
    answers: Mapping[str, str] = draft.answers if template else {GOAL_SUMMARY_KEY: summary}
    if template is None and summary.strip() and not goal_summary_fits(summary):
        # Too long to say as the first sentence: block Start and ask for a shorter goal.
        missing.append(GOAL_SUMMARY_KEY)

    task_vars: dict[str, dict[str, str]] = {}
    for name in _placeholders(task_line):
        answer = answers.get(name, "").strip()
        profile_field = by_key.get(name)
        if answer:
            task_vars[name] = {"literal": answer}
        elif profile_field is not None and profile_field.sharing == "share_freely":
            task_vars[name] = {"field_ref": name}
        else:
            missing.append(name)

    if template:
        missing.extend(
            f.key for f in missing_template_fields(template, full_profile, draft.answers)
        )

    share_keys = list(
        dict.fromkeys([*BASE_SHARED_KEYS, *(template.profile_keys() if template else [])])
    )
    share_keys = [k for k in share_keys if k in by_key]
    # The calling name is required (ON-3): without it the opening line can't be said.
    if CALLING_NAME_KEY not in by_key:
        missing.append(CALLING_NAME_KEY)

    brief: dict[str, Any] = {
        "goal": draft.goal,
        "business_name": draft.business_name,
        "phone_e164": draft.phone_e164,
        "template_key": draft.template_key,
        "task_line": task_line,
        "task_vars": task_vars,
        "constraints": [{"text": t} for t in draft.constraints],
        "preferences": [{"text": t} for t in draft.preferences],
        "fallbacks": [{"text": t} for t in draft.fallbacks],
        "never_agree": [{"text": t} for t in draft.never_agree],
        "share_freely_keys": [k for k in share_keys if by_key[k].sharing == "share_freely"],
        "ask_first_keys": [k for k in share_keys if by_key[k].sharing == "ask_first"],
    }
    return BuiltBrief(brief=brief, missing_fields=list(dict.fromkeys(missing)))
