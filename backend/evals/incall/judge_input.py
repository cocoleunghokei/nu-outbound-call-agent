"""The judge's input for one run, in the XML format JUDGE.md ("What the judge receives") sets.

One file per run: ``<case>`` (brief, setup, expected result, the checks to decide),
``<run_record>`` (every turn and tool call) and ``<script_results>``. ``<`` and ``>`` in any
spoken text or tool argument become look-alikes (``‹`` ``›``), so a transcript can't close
``</run_record>`` or fake a ``<check>``. Only checks with ``judge`` in ``by`` are listed for
deciding; script verdicts are shown as evidence and never re-decided (JUDGE.md rule 6).
"""

import json
from collections.abc import Sequence
from typing import Any

from evals.incall.records import CheckResult, RunRecord
from evals.incall.setup import EvalData, build_case_brief

_ANGLE = str.maketrans({"<": "‹", ">": "›"})
_QUOTE = str.maketrans({"<": "‹", ">": "›", '"': "'"})


def esc(text: str) -> str:
    """Element text with angle brackets swapped for look-alikes."""
    return text.translate(_ANGLE)


def attr(text: str) -> str:
    """Attribute value: angle brackets swapped and double quotes made single."""
    return text.translate(_QUOTE)


def _brief_summary(brief: dict[str, Any], ask_first: Sequence[str]) -> str:
    """The brief as one paragraph. Ask-first lists every ask-first key the prompt names."""

    def items(key: str) -> str:
        return "; ".join(i["text"] for i in brief.get(key) or []) or "none"

    details = "; ".join(f"{d['question']} {d['value']}" for d in brief.get("call_details") or [])
    asked = sorted(set(brief.get("ask_first_keys", [])) | set(ask_first))
    return (
        f"{brief['business_name']}. Goal: {brief['goal']}. "
        f"Hard constraints: {items('constraints')}. Preferences: {items('preferences')}. "
        f"Fallbacks, best first: {items('fallbacks')}. Never agree: {items('never_agree')}. "
        f"Call details: {details or 'none'}. "
        f"Share freely: {', '.join(brief.get('share_freely_keys', []))}. "
        f"Ask first (values not known to the agent): {', '.join(asked)}."
    )


def _setup(case: dict[str, Any]) -> str:
    lines = []
    for n, step in enumerate(case["steps"], start=1):
        if "business" in step:
            lines.append(f'{n}. Business: "{step["business"]}"')
        else:
            detail = step.get("reply") or step.get("text") or step.get("field") or ""
            lines.append(f"{n}. System message: {step['system']} {detail}".rstrip())
    return " ".join(lines)


def judged_checks(case: dict[str, Any], results: Sequence[CheckResult]) -> list[dict[str, str]]:
    """The checks the judge decides for this run: the case's, G1 and the judged reason checks."""
    wanted = []
    for r in results:
        if "judge" in r.by and r.verdict != "n/a":
            scope = next((c.get("scope", "") for c in case["checks"] if c["id"] == r.check), "")
            wanted.append(
                {"id": r.check, "level": r.level, "by": r.by, "text": r.text, "scope": scope}
            )
    return wanted


def render(data: EvalData, record: RunRecord, results: Sequence[CheckResult]) -> str:
    """The judge input for one run, as XML text."""
    case = data.case(record.case_id)
    brief = build_case_brief(data, case)
    checks = judged_checks(case, results)
    lines = [
        f'<case id="{case["id"]}" pass_type="{case["pass_type"]}" brief="{case["brief"]}">',
        f"  <brief>{esc(_brief_summary(brief, list(data.ask_first_values())))}</brief>",
        f"  <setup>{esc(_setup(case))}</setup>",
        *([f"  <note>{esc(case['note'])}</note>"] if case.get("note") else []),
        "  <checks>",
        *(
            f'    <check id="{c["id"]}" level="{c["level"]}" by="{c["by"]}"'
            + (f' scope="{c["scope"]}"' if c["scope"] else "")
            + f">{esc(c['text'])}</check>"
            for c in checks
        ),
        "  </checks>",
        "</case>",
        "",
        f'<run_record case="{record.case_id}" run="{record.run}" model="{attr(record.model)}">',
    ]
    for e in record.events:
        if e.kind == "business":
            lines.append(f'  <turn step="{e.step}" speaker="business">{esc(e.text)}</turn>')
        elif e.kind == "agent":
            lines.append(f'  <turn step="{e.step}" speaker="agent">{esc(e.text)}</turn>')
        elif e.kind == "system":
            lines.append(
                f'  <system_message step="{e.step}" kind="{e.system_kind}">'
                f"{esc(e.text)}</system_message>"
            )
        elif e.kind == "tool_call":
            args = (
                json.dumps(e.args, ensure_ascii=False) if e.args is not None else "(invalid JSON)"
            )
            lines.append(
                f'  <tool_call step="{e.step}" name="{attr(e.tool)}">{esc(args)}</tool_call>'
            )
        else:
            lines.append(
                f'  <tool_result step="{e.step}" name="{attr(e.tool)}">{esc(e.text)}</tool_result>'
            )
    if record.stopped:
        lines.append(f"  <stopped>{esc(record.stopped)}</stopped>")
    if record.error:
        lines.append(f"  <error>{esc(record.error)}</error>")
    lines += ["</run_record>", "", f'<script_results case="{record.case_id}" run="{record.run}">']
    for r in results:
        if r.by == "judge" or r.verdict in ("pending", "n/a"):
            continue
        evidence = f": {r.evidence}" if r.evidence else ""
        lines.append(
            f'  <result check="{r.check}" verdict="{r.verdict}">{esc(r.text + evidence)}</result>'
        )
    lines.append("</script_results>")
    return "\n".join(lines) + "\n"
