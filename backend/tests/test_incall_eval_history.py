"""The eval history page (``make eval-incall-history``), offline with a scripted fake model."""

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path

from evals.incall.history import find_evals, main, render_page, run_status
from evals.incall.records import CheckResult
from evals.incall.run import run_eval
from evals.incall.setup import load_data
from tests.test_incall_eval_runner import Scripted, decision, say, tools

DATA = load_data()
GENERATED = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)


def make_eval(root: Path, name: str, accept: bool) -> None:
    """One results folder with C4-01 and G-10, one run each."""
    replies = [tools(decision("accept"), text="Great, see you then.")] if accept else [say("Hmm.")]
    replies.append(say("Sorry, I can't share that. </details><script>x</script>"))
    cases = [DATA.case("C4-01"), DATA.case("G-10")]
    asyncio.run(run_eval(DATA, cases, Scripted(replies), 1, root / name))


def test_runs_are_grouped_by_date_newest_first_and_open(tmp_path: Path) -> None:
    make_eval(tmp_path, "2026-11-16T1000Z", accept=False)
    make_eval(tmp_path, "2026-11-17T2306Z", accept=True)
    make_eval(tmp_path, "2026-11-17T2325Z", accept=True)
    evals = find_evals(tmp_path)
    assert [e.folder.name for e in evals] == [
        "2026-11-17T2325Z",
        "2026-11-17T2306Z",
        "2026-11-16T1000Z",
    ]

    page = render_page(evals, GENERATED)
    days = re.findall(r'<details class="day"( open)?><summary>(\d{4}-\d{2}-\d{2})', page)
    assert days == [(" open", "2026-11-17"), ("", "2026-11-16")]  # newest day open
    assert page.count('<details class="eval" open>') == 1  # only the newest run open
    assert page.count('<details class="eval"') == 3
    assert "23:25 UTC" in page and "23:06 UTC" in page and "10:00 UTC" in page


def test_each_run_has_the_four_sections_and_escapes_the_transcript(tmp_path: Path) -> None:
    make_eval(tmp_path, "2026-11-17T2306Z", accept=True)
    page = render_page(find_evals(tmp_path), GENERATED)
    for section in ("Summary", "Failures with evidence", "Per-case results", "Transcripts"):
        assert f'<summary class="h">{section}</summary>' in page
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert page.count("</details>") == page.count("<details")  # nothing broke out
    assert "C4-01 · run 1" in page and "G-10 · run 1" in page


def test_the_case_history_shows_a_case_improving(tmp_path: Path) -> None:
    make_eval(tmp_path, "2026-11-16T1000Z", accept=False)  # C4-01 fails
    make_eval(tmp_path, "2026-11-17T2306Z", accept=True)  # C4-01 waits only on the judge
    page = render_page(find_evals(tmp_path), GENERATED)
    trend = page[
        page.index('<table class="trend">') : page.index("</table>", page.index('class="trend"'))
    ]
    row = re.search(r"<tr><td>C4-01</td>(.*?)</tr>", trend)
    assert row is not None
    marks = re.findall(r'class="m (pass|fail|wait)"', row.group(1))
    assert marks == ["fail", "wait"]  # oldest first


def test_waiting_for_the_judge_is_never_shown_as_a_pass() -> None:
    def check(verdict: str) -> CheckResult:
        return CheckResult("C4-01", 1, "x", "x", "hard", "judge", verdict)  # type: ignore[arg-type]

    assert run_status([check("pass"), check("pending")]) == "wait"
    assert run_status([check("pending"), check("fail")]) == "fail"
    assert run_status([check("pass"), check("n/a")]) == "pass"
    assert run_status([check("unclear")]) == "fail"


def test_main_writes_the_page_even_with_no_results(tmp_path: Path) -> None:
    assert main([str(tmp_path)]) == 0
    page = (tmp_path / "history.html").read_text()
    assert "No results yet" in page and "<title>In-call Eval History</title>" in page
