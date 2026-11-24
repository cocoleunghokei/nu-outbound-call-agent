"""Loads cases.json and builds what a live call would give the model, from the real app code.

The brief goes through ``build_brief`` (pre-call), the prompt through ``build_system_prompt``,
the opening line through ``build_first_message`` and the tools through ``tool_definitions``.
Only one thing differs from a live call: Vapi's built-in ``endCall`` tool doesn't exist on the
DeepSeek API, so it is sent as a plain function with no arguments (``END_CALL_TOOL``).
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.briefs.builder import Draft, ProfileField, build_brief
from app.briefs.catalog import get_template
from app.calls.prompt import build_system_prompt
from app.contracts.generated import Brief
from app.voice.prompt_safety import CALLING_NAME_KEY, Snapshot, build_first_message
from app.voice.tools import tool_definitions

CASES_PATH = Path(__file__).with_name("cases.json")

END_CALL_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "endCall",
        "description": "End the phone call. Say goodbye before you call it.",
        "parameters": {"type": "object", "properties": {}},
    },
}


@dataclass(frozen=True)
class EvalData:
    """cases.json, parsed."""

    raw: dict[str, Any]
    profile: list[ProfileField]
    snapshot: Snapshot

    @property
    def cases(self) -> list[dict[str, Any]]:
        """Every case, in file order."""
        return list(self.raw["cases"])

    @property
    def calling_name(self) -> str:
        """The calling name the system messages use (e.g. "Alex Kim")."""
        entry = self.snapshot.get(CALLING_NAME_KEY)
        return entry[0] if entry else "the user"

    def ask_first_values(self) -> dict[str, str]:
        """Ask-first field key to its fake value. These must never be spoken unreleased."""
        return {f.key: f.value for f in self.profile if f.sharing == "ask_first"}

    def case(self, case_id: str) -> dict[str, Any]:
        """One case by ID. Raises KeyError for an unknown ID."""
        for case in self.raw["cases"]:
            if case["id"] == case_id:
                return dict(case)
        raise KeyError(case_id)


@dataclass(frozen=True)
class CallSetup:
    """What the model sees on a live call for one case."""

    system_prompt: str
    first_message: str
    tools: list[dict[str, Any]]


def load_data(path: Path = CASES_PATH) -> EvalData:
    """Read cases.json."""
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    profile = [ProfileField(p["key"], p["value"], p["sharing"]) for p in raw["profile"]]
    snapshot = Snapshot.from_rows([(f.key, f.value, f.sharing) for f in profile])
    return EvalData(raw=raw, profile=profile, snapshot=snapshot)


def build_case_brief(data: EvalData, case: dict[str, Any]) -> dict[str, Any]:
    """The case's ``brief.v1`` brief, built like the app builds it, plus injection overrides.

    Raises ValueError if the brief would still be missing a field (the app would block Start).
    """
    spec = data.raw["briefs"][case["brief"]]
    draft = Draft(
        template_key=spec["template_key"],
        goal=spec["goal"],
        business_name=spec["business_name"],
        phone_e164=spec["phone_e164"],
        answers=spec["answers"],
        constraints=spec["constraints"],
        preferences=spec["preferences"],
        fallbacks=spec["fallbacks"],
        never_agree=spec["never_agree"],
        goal_summary=spec.get("goal_summary"),
    )
    template = get_template(draft.template_key) if draft.template_key else None
    built = build_brief(draft, template, data.profile)
    if built.missing_fields:
        raise ValueError(f"brief {case['brief']} is missing {built.missing_fields}")
    brief = dict(built.brief)
    for key, value in case.get("brief_overrides", {}).items():
        brief[key] = [{"text": v} for v in value] if isinstance(value, list) else value
    return brief


def model_tools() -> list[dict[str, Any]]:
    """The live tool list in Chat Completions form: Vapi-only keys dropped, endCall a function."""
    tools: list[dict[str, Any]] = []
    for tool in tool_definitions():
        if tool.get("type") == "endCall":
            tools.append(END_CALL_TOOL)
        else:
            tools.append({"type": "function", "function": tool["function"]})
    return tools


def call_setup(data: EvalData, case: dict[str, Any]) -> CallSetup:
    """The system prompt, opening line and tools for one case."""
    brief = build_case_brief(data, case)
    return CallSetup(
        system_prompt=build_system_prompt(Brief.model_validate(brief), data.snapshot),
        first_message=build_first_message(brief, data.snapshot),
        tools=model_tools(),
    )
