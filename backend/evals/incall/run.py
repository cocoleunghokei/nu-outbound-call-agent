"""Live in-call eval against the REAL DeepSeek API: ``make eval-incall`` (costs money).

Plays every case in cases.json ``runs_per_case`` times (CASES.md: 65 cases x 3 = 195 runs,
about $0.09 off-peak), one run at a time so the latency numbers aren't skewed by
parallel requests. Then runs the script checks and writes, into a new results folder:

- ``runs.jsonl``: every run, every turn, tool call, timing and token count
- ``script.jsonl``: every check's script verdict (judged checks are ``pending``)
- ``judge/<case>-<run>.xml``: the judge's input for each run (JUDGE.md format)

Next: the judge (Claude Code, following JUDGE.md) writes ``judgments.jsonl`` into the same
folder, then ``make eval-incall-report DIR=<folder>`` writes report.md.

Runs only when ``DEEPSEEK_API_KEY`` is set in the environment. Model and base URL come from
the usual settings (``DEEPSEEK_MODEL``, ``DEEPSEEK_BASE_URL``) unless ``--model`` is given.
Prints progress and failures, never the key or a prompt. Exit 0 when every script check
passed, 1 when any failed, 2 without a key. Not part of ``make test`` or CI.
"""

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from evals.incall.checks import check_run
from evals.incall.judge_input import render
from evals.incall.model import ChatModel, DeepSeekChat
from evals.incall.play import play_case
from evals.incall.records import CheckResult, RunRecord
from evals.incall.setup import EvalData, load_data

RESULTS_DIR = Path(__file__).with_name("results")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """The command-line options."""
    parser = argparse.ArgumentParser(
        prog="python -m evals.incall.run", description=__doc__.splitlines()[0]
    )
    parser.add_argument(
        "--cases", default="", help="comma-separated case IDs or prefixes, e.g. C4-,G-03"
    )
    parser.add_argument(
        "--runs", type=int, default=None, help="runs per case (default: cases.json)"
    )
    parser.add_argument("--model", default="", help="model name (default: DEEPSEEK_MODEL)")
    parser.add_argument(
        "--temperature", type=float, default=None, help="default: unset, the provider's default"
    )
    parser.add_argument("--out", default="", help="results folder (default: results/<UTC time>)")
    return parser.parse_args(argv)


def select_cases(data: EvalData, spec: str) -> list[dict[str, object]]:
    """The cases whose ID equals or starts with one of the comma-separated parts (all if empty)."""
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    if not parts:
        return list(data.cases)
    chosen = [c for c in data.cases if any(c["id"] == p or c["id"].startswith(p) for p in parts)]
    if not chosen:
        raise SystemExit(f"no case matches {spec!r}")
    return chosen


async def run_eval(
    data: EvalData,
    cases: Sequence[dict[str, object]],
    model: ChatModel,
    runs: int,
    folder: Path,
) -> tuple[list[RunRecord], list[CheckResult]]:
    """Play, check and write every run. Returns the records and all check results."""
    (folder / "judge").mkdir(parents=True, exist_ok=True)
    records: list[RunRecord] = []
    results: list[CheckResult] = []
    with (
        (folder / "runs.jsonl").open("w", encoding="utf-8") as runs_out,
        (folder / "script.jsonl").open("w", encoding="utf-8") as script_out,
    ):
        for case in cases:
            for run in range(1, runs + 1):
                record = await play_case(data, dict(case), model, run)
                checks = check_run(data, record)
                runs_out.write(json.dumps(record.to_json(), ensure_ascii=False) + "\n")
                for check in checks:
                    script_out.write(json.dumps(check.to_json(), ensure_ascii=False) + "\n")
                xml = render(data, record, checks)
                (folder / "judge" / f"{record.case_id}-{run}.xml").write_text(xml, encoding="utf-8")
                records.append(record)
                results += checks
                failed = [c for c in checks if c.verdict in ("fail", "unclear")]
                status = "ERROR" if record.error else ("FAIL" if failed else "ok")
                print(f"{status:5} {record.case_id} run {run}", flush=True)
                for c in failed:
                    print(f"      - [{c.level}] {c.check} {c.text}: {c.evidence}", flush=True)
    return records, results


async def main(argv: Sequence[str]) -> int:
    """Run the eval and print where the results are."""
    args = parse_args(argv)
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        print(
            "DEEPSEEK_API_KEY is not set in the environment; not running (a live run costs money)."
        )
        return 2
    settings = Settings()
    data = load_data()
    cases = select_cases(data, args.cases)
    runs = args.runs or int(data.raw["runs_per_case"])
    model = DeepSeekChat(
        key,
        settings.deepseek_base_url,
        args.model or settings.deepseek_model,
        temperature=args.temperature,
    )
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H%MZ")
    folder = Path(args.out) if args.out else RESULTS_DIR / stamp
    print(f"model {model.name}, {len(cases)} cases x {runs} runs, results in {folder}")
    _, results = await run_eval(data, cases, model, runs, folder)
    failed = sum(c.verdict in ("fail", "unclear") for c in results)
    pending = sum(c.verdict == "pending" for c in results)
    print(f"script checks: {failed} failed; {pending} judged checks pending")
    print(f"next: judge the files in {folder / 'judge'} (JUDGE.md), then")
    print(f"      make eval-incall-report DIR={folder}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
