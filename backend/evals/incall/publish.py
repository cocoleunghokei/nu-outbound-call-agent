"""Publish the eval history page to GitHub Pages: ``make eval-incall-publish``.

Rebuilds ``results/history.html`` (same as ``make eval-incall-history``), then commits it as
``index.html`` to the ``gh-pages`` branch and pushes. The branch holds only the generated page,
so it never touches your working tree, your current branch or normal PRs.

Run it after judging an eval, so the page does not show "waiting for the judge".
Everything on the page is public, so the eval cases must use fake data only.

Usage: ``python -m evals.incall.publish [results dir]``.
"""

import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from evals.incall import history
from evals.incall.run import RESULTS_DIR

BRANCH = "gh-pages"
REMOTE = "origin"


def git(cwd: Path, *args: str) -> str:
    """Run one git command in ``cwd`` and return its stdout; raise on failure."""
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def remote_has_branch(cwd: Path, remote: str, branch: str) -> bool:
    """True when ``remote`` already has ``branch``."""
    return bool(git(cwd, "ls-remote", "--heads", remote, branch))


def publish_page(page: Path, source_repo: Path, remote: str = REMOTE) -> bool:
    """Commit ``page`` as ``index.html`` on the gh-pages branch and push it.

    Works in a throwaway clone of ``remote``'s gh-pages branch (or a fresh orphan branch the
    first time). Returns False, pushing nothing, when the page is unchanged.
    """
    url = git(source_repo, "remote", "get-url", remote)
    name = git(source_repo, "config", "user.name")
    email = git(source_repo, "config", "user.email")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        git(work, "init", "--quiet", "--initial-branch", BRANCH)
        git(work, "remote", "add", remote, url)
        if remote_has_branch(work, remote, BRANCH):
            git(work, "pull", "--quiet", remote, BRANCH)
        shutil.copyfile(page, work / "index.html")
        (work / ".nojekyll").write_text("", encoding="utf-8")  # serve the file as is
        git(work, "add", "index.html", ".nojekyll")
        if not git(work, "status", "--porcelain"):
            return False
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        git(
            work,
            "-c", f"user.name={name}",
            "-c", f"user.email={email}",
            "commit", "--quiet", "-m", f"docs: update eval history ({stamp})",
        )  # fmt: skip
        git(work, "push", "--quiet", remote, BRANCH)
    return True


def main(argv: Sequence[str]) -> int:
    """Rebuild the history page and publish it."""
    root = Path(argv[0]) if argv else RESULTS_DIR
    history.main([str(root)])
    if publish_page(root / "history.html", Path.cwd()):
        print(f"published to the {BRANCH} branch; GitHub Pages updates in about a minute")
    else:
        print("page unchanged, nothing to publish")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
