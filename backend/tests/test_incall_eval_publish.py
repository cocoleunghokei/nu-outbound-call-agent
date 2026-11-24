"""Publishing the eval history page to a gh-pages branch, against a local bare "remote"."""

import subprocess
from pathlib import Path

from evals.incall.publish import BRANCH, git, publish_page


def make_repos(tmp_path: Path) -> Path:
    """A bare repo standing in for GitHub, and a source clone with an identity configured."""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(bare)], check=True)
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "--quiet")
    git(source, "config", "user.name", "Test User")
    git(source, "config", "user.email", "test@example.com")
    git(source, "remote", "add", "origin", str(bare))
    return source


def test_first_publish_creates_branch_and_unchanged_page_is_skipped(tmp_path: Path) -> None:
    source = make_repos(tmp_path)
    page = tmp_path / "history.html"
    page.write_text("<html>v1</html>", encoding="utf-8")

    assert publish_page(page, source) is True
    shown = git(source, "--git-dir", str(tmp_path / "remote.git"), "show", f"{BRANCH}:index.html")
    assert shown == "<html>v1</html>"

    assert publish_page(page, source) is False  # same page: no empty commit


def test_second_publish_adds_a_commit_on_top(tmp_path: Path) -> None:
    source = make_repos(tmp_path)
    page = tmp_path / "history.html"
    page.write_text("<html>v1</html>", encoding="utf-8")
    publish_page(page, source)
    page.write_text("<html>v2</html>", encoding="utf-8")

    assert publish_page(page, source) is True
    bare = str(tmp_path / "remote.git")
    assert git(source, "--git-dir", bare, "show", f"{BRANCH}:index.html") == "<html>v2</html>"
    assert git(source, "--git-dir", bare, "rev-list", "--count", BRANCH) == "2"
    assert git(source, "branch", "--list") == ""  # the working repo was never touched
