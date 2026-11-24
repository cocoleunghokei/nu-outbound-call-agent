"""One HTML page documenting every in-call eval run: ``make eval-incall-history``.

Reads every folder in ``evals/incall/results/`` (each one ``make eval-incall`` run) and writes
``results/history.html``: a case-by-case history across runs, then one collapsible toggle per
date (newest first and open), and inside it one toggle per run with

- summary numbers (the same metrics as report.md, from ``report.metrics``),
- every failed or unclear check with its evidence,
- a per-case table (pass, fail or waiting for the judge, per run),
- every transcript, each in its own nested toggle.

Self-contained: no network, no scripts, opens in any browser. Judged checks with no judgment
yet show as "waiting for the judge", never as passed. Re-run after every eval to update it.

Usage: ``python -m evals.incall.history [results dir]``.
"""

import html
import json
import re
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from evals.incall.issues import find_issues, issues_html, notes_for
from evals.incall.records import CheckResult, RunRecord
from evals.incall.report import TARGETS_S, TURN_LABELS, Metrics, Results, cost, load, metrics
from evals.incall.run import RESULTS_DIR
from evals.incall.setup import load_data

_FOLDER_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2})(\d{2})Z")
PASS, FAIL, WAIT = "pass", "fail", "wait"
MARK = {PASS: "✓", FAIL: "✗", WAIT: "…"}
MARK_LABEL = {PASS: "passed", FAIL: "failed", WAIT: "waiting for the judge"}


@dataclass(frozen=True)
class Eval:
    """One results folder: when it ran, and its combined results."""

    folder: Path
    started: datetime
    results: Results
    metrics: Metrics

    @property
    def label(self) -> str:
        """e.g. "23:06 UTC"."""
        return self.started.strftime("%H:%M UTC")

    @property
    def day(self) -> str:
        """e.g. "2026-11-17"."""
        return self.started.strftime("%Y-%m-%d")


def find_evals(root: Path) -> list[Eval]:
    """Every results folder under ``root`` that has a runs.jsonl, newest first."""
    found: list[Eval] = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []:
        if not (folder / "runs.jsonl").exists():
            continue
        results = load(folder)
        found.append(Eval(folder, _started(folder, results.runs), results, metrics(results)))
    return sorted(found, key=lambda e: e.started, reverse=True)


def _started(folder: Path, runs: Sequence[RunRecord]) -> datetime:
    match = _FOLDER_TIME.match(folder.name)
    if match:
        day, hour, minute = match.groups()
        return datetime.fromisoformat(f"{day}T{hour}:{minute}:00+00:00")
    starts = sorted(r.started_at for r in runs)
    return datetime.fromisoformat(starts[0]) if starts else datetime.fromtimestamp(0)


def run_status(checks: Sequence[CheckResult]) -> str:
    """pass, fail, or wait (no failure yet, but the judge hasn't decided everything)."""
    if any(c.verdict in ("fail", "unclear") for c in checks):
        return FAIL
    if any(c.verdict == "pending" for c in checks):
        return WAIT
    return PASS


def _by_run(results: Results) -> dict[tuple[str, int], list[CheckResult]]:
    grouped: dict[tuple[str, int], list[CheckResult]] = defaultdict(list)
    for c in results.checks:
        grouped[(c.case_id, c.run)].append(c)
    return grouped


def case_statuses(results: Results) -> dict[str, list[str]]:
    """Each case's run statuses, in run order."""
    grouped = _by_run(results)
    out: dict[str, list[str]] = defaultdict(list)
    for r in sorted(results.runs, key=lambda r: (r.case_id, r.run)):
        out[r.case_id].append(run_status(grouped[(r.case_id, r.run)]))
    return dict(out)


# --- rendering -----------------------------------------------------------------------------


def e(text: object) -> str:
    """HTML-escaped text."""
    return html.escape(str(text), quote=True)


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def _secs(value: float | None) -> str:
    return f"{value:.1f} s" if value is not None else "n/a"


def _marks(statuses: Sequence[str]) -> str:
    return "".join(
        f'<span class="m {s}" title="run {i}: {MARK_LABEL[s]}">{MARK[s]}</span>'
        for i, s in enumerate(statuses, start=1)
    )


def _badge(ev: Eval) -> str:
    m = ev.metrics
    failed = sum(c.verdict in ("fail", "unclear") for c in ev.results.checks)
    if m.pending:
        return f'<span class="badge wait">{failed} failed · {m.pending} waiting for judge</span>'
    if m.runs and m.hard == m.runs and (not m.soft[1] or m.soft[0] / m.soft[1] >= 0.85):
        return '<span class="badge pass">MVP good</span>'
    return f'<span class="badge fail">{failed} failed checks</span>'


def summary_html(ev: Eval) -> str:
    """The summary numbers for one run."""
    m = ev.metrics
    totals, usd = cost(ev.results.runs)
    models = ", ".join(sorted({r.model for r in ev.results.runs}))
    temps = ", ".join(
        sorted(
            {"default" if r.temperature is None else str(r.temperature) for r in ev.results.runs}
        )
    )
    served = ", ".join(sorted({s for r in ev.results.runs for s in r.served}))
    cases = len({r.case_id for r in ev.results.runs})
    grouped = _by_run(ev.results)
    clean = sum(
        not any(c.level == "hard" and c.verdict in ("fail", "unclear") for c in grouped[key])
        for key in ((r.case_id, r.run) for r in ev.results.runs)
    )
    so_far = (
        f" · {clean} of {m.runs} runs have no Hard failure so far (judge still to decide the rest)"
        if m.pending
        else ""
    )
    runs_per = max((r.run for r in ev.results.runs), default=0)
    rows = [
        ("Model", f"{models} · temperature {temps}"),
        ("Served as", served or "not recorded"),
        ("Size", f"{cases} cases × {runs_per} runs = {m.runs} runs"),
        ("Overall pass", f"{_pct(m.overall, m.runs)} ({m.overall} of {m.runs} runs)"),
        ("Hard pass", f"{_pct(m.hard, m.runs)} ({m.hard} of {m.runs}) · target 100%" + so_far),
        ("Soft pass", f"{_pct(*m.soft)} ({m.soft[0]} of {m.soft[1]} checks) · target 85%"),
        ("Guardrails", f"{_pct(m.guardrails, m.runs)} · target 100%"),
        ("Accuracy", _pct(*m.accuracy)),
        ("Waiting for the judge", f"{m.pending} checks"),
        (
            "Tokens and cost",
            f"{totals.get('prompt', 0):,} in ({totals.get('hit', 0):,} cache hits), "
            f"{totals.get('out', 0):,} out · ${usd:.3f}",
        ),
    ]
    cells = "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in rows)
    lat_rows = ""
    for kind in ("talk", "decision", "after_system"):
        v = m.latency.get(kind, {})
        lat_rows += (
            f"<tr><td>{e(TURN_LABELS[kind])} (≤ {TARGETS_S[kind]} s)</td>"
            f"<td>{v.get('count', 0)}</td>"
            f"<td>{_pct(v.get('within', 0), v.get('count', 0))}</td><td>{_secs(v.get('p50'))}</td>"
            f"<td>{_secs(v.get('p95'))}</td><td>{v.get('dead_air', 0)}</td></tr>"
        )
    return (
        f'<table class="kv">{cells}</table>'
        "<table><thead><tr><th>Turn type</th><th>Turns</th><th>Within target</th><th>P50</th>"
        f"<th>P95</th><th>Dead air</th></tr></thead><tbody>{lat_rows}</tbody></table>"
    )


def failures_html(ev: Eval) -> str:
    """Every failed or unclear check, Hard first."""
    bad = [c for c in ev.results.checks if c.verdict in ("fail", "unclear")]
    if not bad:
        return '<p class="muted">No failed checks.</p>'
    bad.sort(key=lambda c: (c.level != "hard", c.case_id, c.run, c.check))
    rows = "".join(
        f'<tr><td><span class="lvl {c.level}">{e(c.level)}</span></td><td>{e(c.case_id)}</td>'
        f"<td>{c.run}</td><td><code>{e(c.check)}</code></td><td>{e(c.text)}</td>"
        f'<td class="ev">{e(c.evidence or "(no evidence)")}'
        f"{' · unclear' if c.verdict == 'unclear' else ''}</td></tr>"
        for c in bad
    )
    return (
        "<table><thead><tr><th>Level</th><th>Case</th><th>Run</th><th>Check</th>"
        f"<th>Expected</th><th>What happened</th></tr></thead><tbody>{rows}</tbody></table>"
    )


def cases_html(ev: Eval) -> str:
    """Each case with its per-run marks."""
    statuses = case_statuses(ev.results)
    rows = "".join(
        f"<tr><td>{e(cid)}</td><td>{e(ev.results.areas.get(cid, '?'))}</td>"
        f"<td>{sum(s == PASS for s in st)} / {len(st)}</td><td>{_marks(st)}</td></tr>"
        for cid, st in statuses.items()
    )
    return (
        "<table><thead><tr><th>Case</th><th>Area</th><th>Runs passed</th><th>Per run</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )


def transcript_html(run: RunRecord, checks: Sequence[CheckResult]) -> str:
    """One run's conversation as a nested toggle, with its failed checks first."""
    status = run_status(checks)
    lines = []
    for ev in run.events:
        if ev.kind == "business":
            lines.append(f'<div class="t biz"><b>Business</b> {e(ev.text)}</div>')
        elif ev.kind == "agent":
            lines.append(f'<div class="t agent"><b>Agent</b> {e(ev.text)}</div>')
        elif ev.kind == "system":
            lines.append(f'<div class="t sys"><b>System</b> {e(ev.text)}</div>')
        elif ev.kind == "tool_call":
            args = (
                json.dumps(ev.args, ensure_ascii=False) if ev.args is not None else "(invalid JSON)"
            )
            lines.append(f'<div class="t tool"><b>{e(ev.tool)}</b> <code>{e(args)}</code></div>')
    if run.stopped:
        lines.append(f'<div class="t note">Stopped: {e(run.stopped)}</div>')
    if run.error:
        lines.append(f'<div class="t note">Error: {e(run.error)}</div>')
    bad = [c for c in checks if c.verdict in ("fail", "unclear")]
    failed = "".join(f"<li><code>{e(c.check)}</code> {e(c.text)}</li>" for c in bad)
    silences = ", ".join(
        f"step {t.step} {TURN_LABELS[t.type]} {_secs(t.silence_s)}" for t in run.turns
    )
    return (
        f'<details class="run"><summary><span class="m {status}">{MARK[status]}</span> '
        f"{e(run.case_id)} · run {run.run}</summary>"
        + (f'<ul class="fails">{failed}</ul>' if failed else "")
        + "".join(lines)
        + (f'<p class="muted small">Silence: {e(silences)}</p>' if silences else "")
        + "</details>"
    )


def eval_html(ev: Eval, open_: bool) -> str:
    """One run of ``make eval-incall`` as a toggle with its four sections."""
    grouped = _by_run(ev.results)
    transcripts = "".join(
        transcript_html(r, grouped[(r.case_id, r.run)])
        for r in sorted(ev.results.runs, key=lambda r: (r.case_id, r.run))
    )
    return (
        f'<details class="eval"{" open" if open_ else ""}><summary>{e(ev.label)} · '
        f"{len(ev.results.runs)} runs {_badge(ev)}"
        f'<span class="muted small"> {e(ev.folder.name)}</span></summary>'
        f'<details open><summary class="h">Summary</summary>{summary_html(ev)}</details>'
        '<details open><summary class="h">Issues and things to fix</summary>'
        f"{issues_html(find_issues(ev.results.checks), notes_for(ev.folder))}</details>"
        '<details open><summary class="h">Failures with evidence</summary>'
        f"{failures_html(ev)}</details>"
        f'<details><summary class="h">Per-case results</summary>{cases_html(ev)}</details>'
        f'<details><summary class="h">Transcripts</summary>{transcripts}</details>'
        "</details>"
    )


def trend_html(evals: Sequence[Eval]) -> str:
    """Every case across every run, oldest to newest, so progress is visible."""
    ordered = list(reversed(evals))
    per_eval = [case_statuses(ev.results) for ev in ordered]
    case_ids: list[str] = []
    for statuses in per_eval:
        case_ids += [c for c in statuses if c not in case_ids]
    order = {c: i for i, c in enumerate(_case_order())}
    case_ids.sort(key=lambda c: order.get(c, len(order)))
    head = "".join(f"<th>{e(ev.day)}<br>{e(ev.label)}</th>" for ev in ordered)
    rows = ""
    for cid in case_ids:
        cells = "".join(
            f"<td>{_marks(st[cid]) if cid in st else '<span class=muted>–</span>'}</td>"
            for st in per_eval
        )
        rows += f"<tr><td>{e(cid)}</td>{cells}</tr>"
    return (
        '<div class="scroll"><table class="trend"><thead><tr><th>Case</th>'
        f"{head}</tr></thead><tbody>{rows}</tbody></table></div>"
    )


def _case_order() -> list[str]:
    return [c["id"] for c in load_data().cases]


CSS = """
:root{--bg:#fbfaf8;--card:#fff;--text:#1d1d1b;--muted:#6b6a66;--line:#e6e3dd;--pass:#1f7a4d;
--fail:#b3261e;--wait:#8a6d00;--pass-bg:#e5f3ec;--fail-bg:#fbe9e7;--wait-bg:#fbf3d6;--code:#f3f1ec}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--text:#ecebe8;--muted:#a3a19b;
--line:#34332f;--pass:#6fd19c;--fail:#ff8a80;--wait:#e8c75a;--pass-bg:#173326;--fail-bg:#3a1f1c;
--wait-bg:#3a3215;--code:#2a2926}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}
.muted{color:var(--muted)}.small{font-size:12px}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:10px 0;
padding:0 14px}
details[open]{padding-bottom:12px}summary{cursor:pointer;padding:10px 0;font-weight:600}
details details{border-radius:8px}details.day>summary{font-size:18px}
summary.h{font-size:14px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
table.kv th{width:200px;color:var(--muted);font-weight:500}td.ev{word-break:break-word}
code{background:var(--code);padding:1px 5px;border-radius:4px;font-size:12px;word-break:break-word}
.badge{font-size:12px;font-weight:600;padding:2px 8px;border-radius:999px;margin-left:8px}
.badge.pass,.m.pass{color:var(--pass);background:var(--pass-bg)}
.badge.fail,.m.fail{color:var(--fail);background:var(--fail-bg)}
.badge.wait,.m.wait{color:var(--wait);background:var(--wait-bg)}
.m{display:inline-block;width:20px;text-align:center;border-radius:4px;margin-right:2px;font-weight:700}
.lvl{font-size:11px;font-weight:700;text-transform:uppercase}.lvl.hard{color:var(--fail)}
.t{padding:4px 0 4px 10px;border-left:3px solid var(--line);margin:4px 0;font-size:13px}
.t.agent{border-color:var(--pass)}.t.biz{border-color:var(--muted)}.t.sys{border-color:var(--wait)}
.t.tool{border-color:var(--line);color:var(--muted)}.t.note{color:var(--fail)}
.fails{color:var(--fail);font-size:13px;margin:4px 0 8px}.scroll{overflow-x:auto}
table.trend td,table.trend th{white-space:nowrap}
h4{margin:12px 0 4px;font-size:14px}ul.issues{padding-left:18px;font-size:14px}
ul.issues li{margin:6px 0}.lvl.soft{color:var(--wait)}
"""


def render_page(evals: Sequence[Eval], generated: datetime) -> str:
    """The whole history page."""
    by_day: dict[str, list[Eval]] = defaultdict(list)
    for ev in evals:
        by_day[ev.day].append(ev)
    days = ""
    for i, (day, items) in enumerate(by_day.items()):
        runs = sum(len(ev.results.runs) for ev in items)
        inner = "".join(eval_html(ev, open_=(i == 0 and j == 0)) for j, ev in enumerate(items))
        days += (
            f'<details class="day"{" open" if i == 0 else ""}><summary>{e(day)} '
            f'<span class="muted small">· {len(items)} eval run(s), {runs} case runs</span>'
            f"</summary>{inner}</details>"
        )
    body = (
        "<p class='muted'>No results yet. Run <code>make eval-incall</code> first.</p>"
        if not evals
        else (
            f"<details><summary>Case history across runs</summary>"
            f"<p class='muted small'>✓ passed · ✗ failed · … waiting for the judge. "
            f"One mark per run.</p>{trend_html(evals)}</details>{days}"
        )
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>In-call Eval History</title><style>{CSS}</style></head><body><main>"
        "<h1>In-call eval history</h1>"
        f'<p class="muted">{len(evals)} eval run(s). '
        f"Generated {e(generated.strftime('%Y-%m-%d %H:%M UTC'))} "
        "by <code>make eval-incall-history</code>. Scoring rules: JUDGE.md.</p>"
        f"{body}</main></body></html>\n"
    )


def main(argv: Sequence[str]) -> int:
    """Write history.html into the results folder and print its path."""
    root = Path(argv[0]) if argv else RESULTS_DIR
    evals = find_evals(root)
    root.mkdir(parents=True, exist_ok=True)
    out = root / "history.html"
    out.write_text(render_page(evals, datetime.now(UTC)), encoding="utf-8")
    print(f"{len(evals)} eval run(s) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
