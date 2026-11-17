"""The in-call eval's ``cases.json`` stays in step with CASES.md and the real code. Fake data.

No model is called. These checks catch a case file that would make the eval runner (or the
judge) score against something that doesn't exist: an ID missing from CASES.md, a brief that
doesn't build, a field key or enum value the tools don't have, or a reply step with no
question to answer.
"""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.briefs.builder import Draft, ProfileField, build_brief
from app.briefs.catalog import get_template
from app.calls.prompt import build_system_prompt
from app.contracts import load_schema, validator_for
from app.contracts.generated import Brief
from app.voice.prompt_safety import Snapshot, build_first_message

EVAL_DIR = Path(__file__).resolve().parent.parent / "evals" / "incall"
DATA: dict[str, Any] = json.loads((EVAL_DIR / "cases.json").read_text())
CASES: list[dict[str, Any]] = DATA["cases"]
PROFILE = [ProfileField(p["key"], p["value"], p["sharing"]) for p in DATA["profile"]]
SNAPSHOT = Snapshot.from_rows([(f.key, f.value, f.sharing) for f in PROFILE])
ASK_FIRST = {f.key: f.value for f in PROFILE if f.sharing == "ask_first"}

TOOLS = {"ask_user", "report_decision", "report_outcome", "endCall"}
STEP_KINDS = {"answer", "field_shared", "field_declined", "timeout", "instruction"}
NEEDS_OPEN_PING = {"answer", "field_shared", "field_declined"}
LEVELS = {"hard", "soft"}
CHECKERS = {"script", "judge", "script+judge"}
LENSES = {"accuracy", "guardrails", "latency"}
OPS = {
    "tool_called",
    "tool_not_called",
    "no_tool",
    "says",
    "says_none",
    "order",
    "not_before",
    "arg_matches",
    "arg_absent",
    "injection_flagged",
}


def built_brief(name: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a case brief the way the app does, then apply a case's injection overrides."""
    spec = DATA["briefs"][name]
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
    result = build_brief(draft, template, PROFILE)
    assert result.missing_fields == [], (name, result.missing_fields)
    brief = dict(result.brief)
    for key, value in (overrides or {}).items():
        brief[key] = [{"text": v} for v in value] if isinstance(value, list) else value
    return brief


def all_checks() -> Iterator[tuple[str, dict[str, Any]]]:
    for case in CASES:
        for check in case["checks"]:
            yield case["id"], check


def _arg_paths(node: dict[str, Any], prefix: str = "") -> dict[str, set[Any]]:
    """Allowed values per dotted arg path of a JSON Schema; an empty set means free text."""
    paths: dict[str, set[Any]] = {}
    for key, prop in node.get("properties", {}).items():
        path = f"{prefix}{key}"
        if "enum" in prop:
            paths[path] = set(prop["enum"])
        elif prop.get("type") == "boolean":
            paths[path] = {True, False}
        elif prop.get("type") == "object":
            paths.update(_arg_paths(prop, f"{path}."))
        else:
            paths[path] = set()
    return paths


def tool_enums() -> dict[str, dict[str, set[Any]]]:
    """Allowed values per dotted arg path for each tool, from contracts/tools/*.args.v1.json."""
    return {
        tool: _arg_paths(load_schema(f"tools/{tool}.args.v1.json"))
        for tool in ("ask_user", "report_decision", "report_outcome")
    }


# --- in step with CASES.md -------------------------------------------------------------------


def test_case_ids_match_cases_md_exactly() -> None:
    doc = (EVAL_DIR / "CASES.md").read_text()
    in_doc = re.findall(r"^\| ((?:C4|C6|G|T|R|PI|SM|TP)-\d+) \|", doc, flags=re.MULTILINE)
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), "duplicate case id"
    assert ids == in_doc


def test_totals_match_cases_md() -> None:
    doc = (EVAL_DIR / "CASES.md").read_text()
    match = re.search(
        r"(\d+) cases × 3 runs = \d+ runs per eval\. (\d+) cases are \*\*Hard\*\* and "
        r"(\d+) are Soft\. (\d+) cases carry the \*\*GR\*\* tag",
        doc,
    )
    assert match, "the Totals line in CASES.md changed shape"
    total, hard, soft, gr = (int(n) for n in match.groups())
    assert len(CASES) == total
    assert sum(c["pass_type"] == "hard" for c in CASES) == hard
    assert sum(c["pass_type"] == "soft" for c in CASES) == soft
    assert sum(c["gr"] for c in CASES) == gr
    assert DATA["runs_per_case"] == 3


# --- briefs build the way the app builds them ------------------------------------------------


@pytest.mark.parametrize("name", sorted(DATA["briefs"]))
def test_every_brief_builds_validates_and_hides_ask_first_values(name: str) -> None:
    brief = built_brief(name)
    assert list(validator_for("brief.v1.json").iter_errors(brief)) == []
    prompt = build_system_prompt(Brief.model_validate(brief), SNAPSHOT)
    first = build_first_message(brief, SNAPSHOT)
    for value in ASK_FIRST.values():
        assert value not in prompt and value not in first


INJECTED = [c for c in CASES if "brief_overrides" in c]


@pytest.mark.parametrize("case", INJECTED, ids=lambda c: c["id"])
def test_injection_overrides_still_build_a_prompt(case: dict[str, Any]) -> None:
    brief = Brief.model_validate(built_brief(case["brief"], case["brief_overrides"]))
    prompt = build_system_prompt(brief, SNAPSHOT)
    assert prompt.count("</brief>") == 1 and prompt.count("</rules>") == 1


def test_the_profile_is_fake_and_has_no_email() -> None:
    # G-05 relies on there being no email anywhere, so any email spoken is invented.
    assert not any("@" in f.value for f in PROFILE)
    assert all("555" in f.value for f in PROFILE if f.key == "phone")


# --- every case is well formed ---------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_case_fields_are_valid(case: dict[str, Any]) -> None:
    assert case["brief"] in DATA["briefs"]
    assert case["pass_type"] in LEVELS
    assert case["lenses"] and set(case["lenses"]) <= LENSES
    assert case["gr"] is ("guardrails" in case["lenses"]), "GR cases carry the guardrails lens"
    assert case["steps"], "a case needs at least one step"
    if case["pass_type"] == "hard":
        assert any(c["level"] == "hard" for c in case["checks"]), "a Hard case needs a Hard check"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_steps_are_valid_and_replies_follow_a_question(case: dict[str, Any]) -> None:
    for i, step in enumerate(case["steps"]):
        assert len(step) in (1, 2, 3), step
        if "business" in step:
            assert isinstance(step["business"], str) and step["business"].strip()
            continue
        kind = step["system"]
        assert kind in STEP_KINDS, kind
        if kind in NEEDS_OPEN_PING or kind == "timeout":
            assert i > 0, f"{kind} needs an earlier business turn to ping about"
        if kind == "answer":
            assert step["reply"].strip()
        if kind in ("field_shared", "field_declined"):
            assert step["field"] in ASK_FIRST
        if kind == "instruction":
            assert step["text"].strip()


def test_check_ids_levels_and_checkers_are_valid() -> None:
    ids = [check["id"] for _, check in all_checks()]
    assert len(ids) == len(set(ids))
    for case_id, check in all_checks():
        assert check["id"].startswith(f"{case_id}.")
        assert check["level"] in LEVELS and check["by"] in CHECKERS
        assert check["text"].strip()
        assert ("assert" in check) is (check["by"] != "judge"), check["id"]


def test_check_scopes_point_at_a_real_step() -> None:
    steps = {c["id"]: len(c["steps"]) for c in CASES}
    for case_id, check in all_checks():
        scope = check.get("scope", f"step:{steps[case_id]}")
        if scope == "run":
            continue
        n = int(scope.removeprefix("step:"))
        assert 1 <= n <= steps[case_id], check["id"]
    for case in CASES:
        for check in case["checks"]:
            test = check.get("assert", {})
            if test.get("op") == "injection_flagged":
                assert "business" in case["steps"][test["step"] - 1], check["id"]


def test_script_checks_use_known_ops_tools_and_contract_values() -> None:
    enums = tool_enums()
    for _, check in all_checks():
        test = check.get("assert")
        if test is None:
            continue
        assert test["op"] in OPS, check["id"]
        named = [test.get("tool"), *test.get("tools", [])]
        for part in ("first", "then"):
            if part in test:
                named.append(test[part]["tool"])
        assert {t for t in named if t} <= TOOLS, check["id"]
        args = dict(test.get("args", {}))
        for part in ("first", "then"):
            args.update(test.get(part, {}).get("args", {}))
        tool = test.get("tool") or test.get("first", {}).get("tool")
        for path, value in args.items():
            allowed = enums[tool].get(path)
            assert allowed is not None, f"{check['id']}: {tool} has no arg {path}"
            for v in value if isinstance(value, list) else [value]:
                assert not allowed or v in allowed, f"{check['id']}: {path}={v!r}"
        if test.get("op") in ("arg_matches", "arg_absent"):
            assert test["field"] in enums[test["tool"]], check["id"]
        if path_value := args.get("field_requested"):
            assert path_value in ASK_FIRST, check["id"]


def test_regexes_compile() -> None:
    patterns = [p for _, c in all_checks() for p in c.get("assert", {}).get("any", [])]
    patterns += [p for g in DATA["global_checks"] for p in g["assert"].get("any", [])]
    for pattern in patterns:
        re.compile(pattern, re.IGNORECASE)


def test_global_checks_are_g1_to_g5_and_hard() -> None:
    assert [g["id"] for g in DATA["global_checks"]] == ["G1", "G2", "G3", "G4", "G5"]
    assert all(g["level"] == "hard" for g in DATA["global_checks"])
