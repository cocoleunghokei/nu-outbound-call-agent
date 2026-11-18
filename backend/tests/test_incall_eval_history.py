"""The eval history page (``make eval-incall-history``), offline with a scripted fake model."""

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path

from evals.incall.history import find_evals, main, render_page, run_status
from evals.incall.records import CheckResult
from evals.incall.run import run_eval
from evals.incall.setup import load_data
from tests.test_incall_eval_runner import THU_2PM, Scripted, decision, say, tools

DATA = load_data()
GENERATED = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)


def make_eval(root: Path, name: str, accept: bool) -> None:
    """One results folder with C4-01 and G-10, one run each."""
    accept_2pm = tools(decision("accept", offer=THU_2PM), text="Great, see you then.")
    replies = [accept_2pm] if accept else [say("Hmm.")]
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


# --- issues and things to fix --------------------------------------------------------------


def _failed(check: str, text: str, level: str, evidence: str, case: str = "C4-03") -> CheckResult:
    return CheckResult(case, 1, check, text, level, "script", "fail", evidence)  # type: ignore[arg-type]


def test_failures_are_grouped_into_issues_hard_first() -> None:
    from evals.incall.issues import find_issues

    checks = [
        _failed("D5-len", "Reasons", "soft", "101 chars", "C4-03"),
        _failed("D5-len", "Reasons", "soft", "94 chars", "C4-09"),
        _failed(
            "C4-03.3", "Counters first: report_decision action=counter", "soft", "no tool call"
        ),
        _failed("C4-01.1", "report_decision accept", "hard", "no tool call", "C4-01"),
        _failed(
            "script-complete", "Follows the script", "hard", "step 3: no open ask_user", "R-01"
        ),
        CheckResult("C4-02", 1, "C4-02.1", "x", "hard", "script", "pass"),
    ]
    issues = find_issues(checks)
    titles = [i.title for i in issues]
    assert titles[:2] == [
        "Left the script before the end",
        "Talked but didn't call the expected tool",
    ]
    assert issues[0].hard and not issues[-1].hard
    reasons = next(i for i in issues if i.title == "Reasons over 80 characters")
    assert reasons.checks == 2 and reasons.cases == ["C4-03", "C4-09"]
    assert "Countered in words without logging it (Q9)" in titles


def test_notes_come_from_git_first_then_the_results_folder(tmp_path: Path) -> None:
    from evals.incall.issues import notes_for

    folder = tmp_path / "results" / "2026-11-17T2306Z"
    folder.mkdir(parents=True)
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    assert notes_for(folder, notes_dir) is None
    (folder / "notes.md").write_text("local")
    assert notes_for(folder, notes_dir) == "local"
    (notes_dir / "2026-11-17T2306Z.md").write_text("in git")
    assert notes_for(folder, notes_dir) == "in git"


def test_notes_markdown_is_escaped_html() -> None:
    from evals.incall.issues import notes_html

    out = notes_html("## Found\n- **C4-01** said `checking` <script>\nPlain line.")
    assert out == (
        "<h4>Found</h4><ul><li><b>C4-01</b> said <code>checking</code> &lt;script&gt;</li></ul>"
        "<p>Plain line.</p>"
    )


def test_the_report_and_the_page_show_issues_and_notes(tmp_path: Path) -> None:
    from evals.incall.report import load
    from evals.incall.report import render as render_report

    make_eval(tmp_path, "2026-11-15T0900Z", accept=False)
    results = load(tmp_path / "2026-11-15T0900Z")
    text, _ = render_report(results, "## What we found\n- C4-01 never accepted.")
    assert "## Issues and things to fix" in text and "### Notes and fixes" in text
    assert "**[Must-pass] Talked but didn't call the expected tool**" in text
    assert "- C4-01 never accepted." in text

    (tmp_path / "2026-11-15T0900Z" / "notes.md").write_text("## Fixes made\n- Prompt line added.")
    page = render_page(find_evals(tmp_path), GENERATED)
    assert '<summary class="h">Issues and things to fix</summary>' in page
    assert "<h4>Fixes made</h4><ul><li>Prompt line added.</li></ul>" in page


def test_every_committed_note_names_a_results_folder() -> None:
    from evals.incall.issues import NOTES_DIR

    for path in NOTES_DIR.glob("*.md"):
        if path.name not in ("README.md", "ISSUES.md"):
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{4}Z\.md", path.name), path.name


def test_notes_markdown_renders_a_pipe_table() -> None:
    from evals.incall.issues import notes_html

    out = notes_html("| Issue | Fix |\n| --- | --- |\n| **No tool call** | `rule` <b> |\nAfter.")
    assert "<th>Issue</th><th>Fix</th>" in out
    assert "<td><b>No tool call</b></td><td><code>rule</code> &lt;b&gt;</td>" in out
    assert "---" not in out and out.endswith("<p>After.</p>")


def test_the_page_opens_with_the_issues_summary(tmp_path: Path) -> None:
    summary = tmp_path / "ISSUES.md"
    summary.write_text("## Biggest issues and fixes\n| Issue | Fix |\n| --- | --- |\n| A | B |")
    page = render_page([], GENERATED, summary_path=summary)
    assert '<section class="summary"><h4>Biggest issues and fixes</h4>' in page
    assert "<td>A</td><td>B</td>" in page
    assert '<section class="summary">' not in render_page([], GENERATED, tmp_path / "none.md")


def test_the_committed_issues_summary_renders() -> None:
    from evals.incall.history import SUMMARY_FILE, summary_section

    section = summary_section(SUMMARY_FILE)
    assert "<th>Issue</th>" in section and "No tool call" in section


def test_the_page_defines_must_pass_and_quality(tmp_path: Path) -> None:
    page = render_page([], GENERATED, tmp_path / "none.md")
    assert "<h4>How to read the scores</h4>" in page
    assert "Must-pass rate (Hard pass)" in page and "Quality score (Soft pass)" in page
    assert "One failure fails the whole run" in page and "Counted per check" in page
