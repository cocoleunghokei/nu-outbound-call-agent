"""Builds report.md from one eval's results (JUDGE.md steps 4 and 5): ``make eval-incall-report``.

Reads ``runs.jsonl`` and ``script.jsonl`` from the runner and, once the judge has run,
``judgments.jsonl`` (one line per judged check: case, run, check, verdict, evidence, note).
It combines them as JUDGE.md says:

- a ``script+judge`` check fails if either fails; ``unclear`` counts as a fail;
- a judged check with no judgment yet is ``pending``, and the verdict says the report is
  incomplete instead of guessing;
- the five metrics, then every Hard failure with its transcript, pass rate per area and per
  case (1 or 2 of 3 is "inconsistent"), Soft failures grouped, latency, unclear verdicts,
  and the model, temperature, tokens and cost.

Usage: ``python -m evals.incall.report <results dir>``. Exit 0 when the MVP verdict is good,
1 when not, 3 when judging is incomplete.
"""

import json
import math
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from evals.incall.issues import find_issues, issues_markdown, notes_for
from evals.incall.records import CheckResult, RunRecord
from evals.incall.setup import CASES_PATH

# Provisional P95 targets by turn type, in seconds (CASES.md Q5).
TARGETS_S = {"talk": 2.0, "decision": 3.5, "after_system": 2.0}
TURN_LABELS = {"talk": "talk-only", "decision": "decision", "after_system": "after system"}
DEAD_AIR_S = 5.0
HARD_TARGET = 1.0
SOFT_TARGET = 0.85
# DeepSeek USD per 1M tokens, checked 17 Nov 2026 (CASES.md, cost estimate).
PRICES = {
    "off_peak": {"hit": 0.003, "miss": 0.15, "out": 0.60},
    "peak": {"hit": 0.006, "miss": 0.30, "out": 1.20},
}
PEAK_HOURS_UTC = {1, 2, 3, 6, 7, 8, 9}  # 01:00-04:00 and 06:00-10:00, Monday to Friday


@dataclass(frozen=True)
class Results:
    """One eval's runs and final check verdicts."""

    runs: list[RunRecord]
    checks: list[CheckResult]
    areas: dict[str, str]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Every JSON line of a file, or [] when it doesn't exist."""
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def combine(script: dict[str, Any], judged: dict[str, Any] | None) -> CheckResult:
    """The final verdict of one check from its script result and its judgment, if any."""
    result = CheckResult(**script)
    if result.verdict == "n/a" or result.by == "script":
        return result
    if judged is None:
        if result.by == "judge" or result.verdict == "pass":
            return replace(result, verdict="pending")
        return result  # the script already failed it
    judge_verdict = judged.get("verdict", "unclear")
    if judge_verdict not in ("pass", "fail", "unclear"):
        judge_verdict = "unclear"
    evidence = judged.get("evidence", "")
    if result.by == "judge":
        return replace(result, verdict=judge_verdict, evidence=evidence)
    if "fail" in (result.verdict, judge_verdict):
        verdict = "fail"
    elif "unclear" in (result.verdict, judge_verdict):
        verdict = "unclear"
    else:
        verdict = "pass"
    joined = "; ".join(e for e in (result.evidence, evidence) if e)
    return replace(result, verdict=verdict, evidence=joined)  # type: ignore[arg-type]


def load(folder: Path, cases_path: Path | None = None) -> Results:
    """Read one results folder and combine script and judge verdicts."""
    runs = [RunRecord.from_json(r) for r in read_jsonl(folder / "runs.jsonl")]
    judged = {(j["case"], j["run"], j["check"]): j for j in read_jsonl(folder / "judgments.jsonl")}
    checks = [
        combine(s, judged.get((s["case_id"], s["run"], s["check"])))
        for s in read_jsonl(folder / "script.jsonl")
    ]
    raw = json.loads((cases_path or CASES_PATH).read_text(encoding="utf-8"))
    areas = {c["id"]: c["area"] for c in raw["cases"]}
    return Results(runs=runs, checks=checks, areas=areas)


def percentile(values: Sequence[float], p: float) -> float | None:
    """Nearest-rank percentile (P50 = the typical value, P95 = only 1 in 20 is slower)."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def _ok(check: CheckResult) -> bool:
    return check.verdict in ("pass", "n/a")


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def _secs(value: float | None) -> str:
    return f"{value:.1f} s" if value is not None else "n/a"


@dataclass(frozen=True)
class Metrics:
    """The numbers at the top of the report."""

    runs: int
    overall: int
    accuracy: tuple[int, int]
    guardrails: int
    hard: int
    soft: tuple[int, int]
    pending: int
    latency: dict[str, dict[str, Any]]


def metrics(results: Results) -> Metrics:
    """The five metrics of JUDGE.md, plus latency by turn type."""
    by_run: dict[tuple[str, int], list[CheckResult]] = defaultdict(list)
    for c in results.checks:
        by_run[(c.case_id, c.run)].append(c)
    keys = [(r.case_id, r.run) for r in results.runs]
    accuracy_runs = [k for k in keys if any(c.accuracy for c in by_run[k])]
    soft = [c for c in results.checks if c.level == "soft" and c.verdict not in ("pending", "n/a")]
    latency: dict[str, dict[str, Any]] = {}
    all_turns = [t for r in results.runs for t in r.turns if t.silence_s is not None]
    for kind in ("talk", "decision", "after_system"):
        values = [t.silence_s for t in all_turns if t.type == kind and t.silence_s is not None]
        latency[kind] = {
            "count": len(values),
            "within": sum(v <= TARGETS_S[kind] for v in values),
            "p50": percentile(values, 50),
            "p95": percentile(values, 95),
            "dead_air": sum(v > DEAD_AIR_S for v in values),
        }
    decisions = [t for r in results.runs for t in r.turns if t.type == "decision"]
    latency["overall"] = {
        "count": len(all_turns),
        "within": sum(v["within"] for v in latency.values()),
        "p50": percentile([t.silence_s for t in all_turns if t.silence_s is not None], 50),
        "p95": percentile([t.silence_s for t in all_turns if t.silence_s is not None], 95),
        "dead_air": sum(v["dead_air"] for v in latency.values()),
        "tool_first": (sum(t.tool_before_speaking for t in decisions), len(decisions)),
    }
    return Metrics(
        runs=len(keys),
        overall=sum(all(_ok(c) for c in by_run[k]) for k in keys),
        accuracy=(
            sum(all(_ok(c) for c in by_run[k] if c.accuracy) for k in accuracy_runs),
            len(accuracy_runs),
        ),
        guardrails=sum(all(_ok(c) for c in by_run[k] if c.guardrail) for k in keys),
        hard=sum(all(_ok(c) for c in by_run[k] if c.level == "hard") for k in keys),
        soft=(sum(c.verdict == "pass" for c in soft), len(soft)),
        pending=sum(c.verdict == "pending" for c in results.checks),
        latency=latency,
    )


def verdict_line(m: Metrics) -> tuple[str, int]:
    """The MVP verdict and the exit code: 0 good, 1 not yet, 3 judging incomplete."""
    hard = m.hard / m.runs if m.runs else 0.0
    soft = m.soft[0] / m.soft[1] if m.soft[1] else 1.0
    if m.pending:
        return f"⏳ Incomplete: {m.pending} checks still need the judge (Hard so far {hard:.1%})", 3
    if hard >= HARD_TARGET and soft >= SOFT_TARGET:
        return "✅ MVP good", 0
    return f"❌ Not yet (Hard {hard:.1%}, target 100%; Soft {soft:.1%}, target 85%)", 1


def cost(runs: Iterable[RunRecord]) -> tuple[dict[str, int], float]:
    """Token totals and the USD cost, priced by each run's start hour (peak or off-peak)."""
    totals: Counter[str] = Counter()
    usd = 0.0
    for run in runs:
        start = datetime.fromisoformat(run.started_at)
        band = "peak" if start.weekday() < 5 and start.hour in PEAK_HOURS_UTC else "off_peak"
        price = PRICES[band]
        for u in run.usage:
            prompt = u.get("prompt_tokens", 0)
            hit = u.get("prompt_cache_hit_tokens", 0)
            miss = u.get("prompt_cache_miss_tokens", prompt - hit)
            out = u.get("completion_tokens", 0)
            totals.update({"prompt": prompt, "hit": hit, "miss": miss, "out": out})
            usd += (hit * price["hit"] + miss * price["miss"] + out * price["out"]) / 1e6
    return dict(totals), usd


def transcript(run: RunRecord) -> list[str]:
    """A run as readable lines, for the Hard-failure section."""
    lines = []
    for e in run.events:
        if e.kind == "business":
            lines.append(f"    Business: {e.text}")
        elif e.kind == "agent":
            lines.append(f"    Agent:    {e.text}")
        elif e.kind == "system":
            lines.append(f"    System:   {e.text}")
        elif e.kind == "tool_call":
            lines.append(f"    Tool:     {e.tool} {json.dumps(e.args, ensure_ascii=False)}")
    if run.stopped:
        lines.append(f"    (stopped: {run.stopped})")
    if run.error:
        lines.append(f"    (error: {run.error})")
    return lines


def render(results: Results, notes: str | None = None) -> tuple[str, int]:
    """report.md text and the exit code. ``notes`` is the run's write-up, if any."""
    m = metrics(results)
    verdict, code = verdict_line(m)
    lat = m.latency
    out = ["# In-call eval report", "", f"MVP verdict: {verdict}", "", "```"]
    out.append(
        f"1. Overall pass           {_pct(m.overall, m.runs):>6}   ({m.overall} of {m.runs} runs)"
    )
    out.append(f"2. Lenses   Accuracy      {_pct(*m.accuracy):>6}")
    out.append(f"            Guardrails    {_pct(m.guardrails, m.runs):>6}   target 100%")
    out.append(
        f"            Latency       {_pct(lat['overall']['within'], lat['overall']['count']):>6}"
        "   (provisional targets)"
    )
    for i, kind in enumerate(("talk", "decision", "after_system")):
        v = lat[kind]
        label = ("3. Latency  " if i == 0 else "            ") + f"{TURN_LABELS[kind]:<13}"
        out.append(
            f"{label} {_pct(v['within'], v['count']):>6}   "
            f"P50 {_secs(v['p50'])}  P95 {_secs(v['p95'])}"
            + (f"  dead air: {v['dead_air']}" if v["dead_air"] else "")
        )
    out.append(
        f"4. Hard pass              {_pct(m.hard, m.runs):>6}   "
        f"({m.hard} of {m.runs} runs)   target 100%"
    )
    out.append(
        f"5. Soft pass              {_pct(*m.soft):>6}   "
        f"({m.soft[0]} of {m.soft[1]} checks)  target 85%"
    )
    out += ["```", ""]
    out += issues_markdown(find_issues(results.checks), notes)

    runs = {(r.case_id, r.run): r for r in results.runs}
    hard_fails = [
        c for c in results.checks if c.level == "hard" and c.verdict in ("fail", "unclear")
    ]
    out.append(f"## Hard failures ({len(hard_fails)})")
    out.append("")
    for c in hard_fails:
        out += [f"**{c.case_id} run {c.run}** · {c.text} · {c.by} · {c.verdict}", ""]
        out += [f"Evidence: {c.evidence or '(none)'}", "", "```"]
        out += transcript(runs[(c.case_id, c.run)]) if (c.case_id, c.run) in runs else []
        out += ["```", ""]

    out += _areas(results)
    out += _soft_failures(results)
    tf_count, tf_total = lat["overall"]["tool_first"]
    out += [
        "## Latency",
        "",
        "| Turn type | Turns | Within target | P50 | P95 | Dead air (over 5 s) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for kind in ("talk", "decision", "after_system", "overall"):
        v = lat[kind]
        name = TURN_LABELS.get(kind, "overall")
        target = f" (≤ {TARGETS_S[kind]} s)" if kind in TARGETS_S else ""
        out.append(
            f"| {name}{target} | {v['count']} | {_pct(v['within'], v['count'])} "
            f"| {_secs(v['p50'])} "
            f"| {_secs(v['p95'])} | {v['dead_air']} |"
        )
    pings = [
        t.time_to_ping_s for r in results.runs for t in r.turns if t.time_to_ping_s is not None
    ]
    out += [
        "",
        f"Tool called without speaking first: {_pct(tf_count, tf_total)} of decision turns "
        f"({tf_count} of {tf_total}). Time to ping: P50 {_secs(percentile(pings, 50))}, "
        f"P95 {_secs(percentile(pings, 95))}.",
        "",
        "Model time only: Vapi's endpointing and text-to-speech come on top, so these are a floor.",
        "",
    ]
    unclear = [c for c in results.checks if c.verdict == "unclear"]
    pending = [c for c in results.checks if c.verdict == "pending"]
    out += [f"## Unclear verdicts, for Lotto to settle ({len(unclear)})", ""]
    out += [f"- {c.case_id} run {c.run} · {c.text} · {c.evidence}" for c in unclear] or ["None."]
    if pending:
        out += ["", f"## Still pending the judge ({len(pending)})", ""]
        out += [f"- {c.case_id} run {c.run} · {c.check} · {c.text}" for c in pending[:50]]
        out += [f"- ...and {len(pending) - 50} more"] if len(pending) > 50 else []
    totals, usd = cost(results.runs)
    models = sorted({r.model for r in results.runs})
    temps = sorted(
        {
            str(r.temperature) if r.temperature is not None else "provider default"
            for r in results.runs
        }
    )
    starts = sorted(r.started_at for r in results.runs)
    stopped = [r for r in results.runs if r.stopped]
    errors = [r for r in results.runs if r.error]
    out += [
        "",
        "## Run info",
        "",
        f"- Model: {', '.join(models) or 'n/a'} · temperature: {', '.join(temps) or 'n/a'}",
        f"- Started: {starts[0] if starts else 'n/a'} to {starts[-1] if starts else 'n/a'} (UTC)",
        f"- Tokens: {totals.get('prompt', 0):,} in ({totals.get('hit', 0):,} cache hits), "
        f"{totals.get('out', 0):,} out · cost ${usd:.2f}",
        f"- Runs that left the script early: {len(stopped)} · "
        f"runs with a model error: {len(errors)}",
        "",
    ]
    return "\n".join(out), code


def _areas(results: Results) -> list[str]:
    by_run: dict[tuple[str, int], list[CheckResult]] = defaultdict(list)
    for c in results.checks:
        by_run[(c.case_id, c.run)].append(c)
    per_case: dict[str, list[bool]] = defaultdict(list)
    for r in results.runs:
        per_case[r.case_id].append(all(_ok(c) for c in by_run[(r.case_id, r.run)]))
    per_area: dict[str, list[bool]] = defaultdict(list)
    for case_id, passes in per_case.items():
        per_area[results.areas.get(case_id, "?")] += passes
    out = ["## Pass rate per area", "", "| Area | Runs passed |", "| --- | --- |"]
    out += [f"| {a} | {sum(p)} / {len(p)} |" for a, p in per_area.items()]
    shaky = [f"{c} {sum(p)}/{len(p)}" for c, p in per_case.items() if 0 < sum(p) < len(p)]
    failed = [f"{c} 0/{len(p)}" for c, p in per_case.items() if p and not any(p)]
    out += ["", f"Inconsistent cases (passed some runs, not all): {', '.join(shaky) or 'none'}"]
    out += [f"Cases that failed every run: {', '.join(failed) or 'none'}", ""]
    return out


def _soft_failures(results: Results) -> list[str]:
    groups: dict[str, list[str]] = defaultdict(list)
    for c in results.checks:
        if c.level == "soft" and c.verdict in ("fail", "unclear"):
            groups[c.text].append(f"{c.case_id}/{c.run}")
    out = ["## Soft failures, grouped by check", ""]
    for text, where in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        out.append(
            f"- {text}: {len(where)} times "
            f"({', '.join(where[:8])}{', ...' if len(where) > 8 else ''})"
        )
    return out + (["None."] if not groups else []) + [""]


def main(argv: Sequence[str]) -> int:
    """Write report.md into the results folder and print the verdict."""
    if len(argv) != 1:
        print("usage: python -m evals.incall.report <results dir>")
        return 2
    folder = Path(argv[0])
    text, code = render(load(folder), notes_for(folder))
    (folder / "report.md").write_text(text, encoding="utf-8")
    print(text.splitlines()[2])
    print(f"wrote {folder / 'report.md'}")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
