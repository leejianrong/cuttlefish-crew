from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cuttlefish.sessions import worktree

NOW = datetime(2026, 10, 31, 9, 5, tzinfo=UTC)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "first")
    return tmp_path


def test_slug_is_branch_safe_short_and_never_empty() -> None:
    assert (
        worktree.slug("Add a /summary page, please!", now=NOW)
        == "add-a-summary-page-please-1031-0905"
    )
    assert worktree.slug("???", now=NOW) == "goal-1031-0905"
    assert len(worktree.slug("x" * 500, now=NOW)) <= 50


def test_create_makes_a_branch_and_a_worktree_inside_the_root(repo: Path) -> None:
    here = _git(repo, "branch", "--show-current").strip()
    branch, path = worktree.create(str(repo), "Add a summary", now=NOW)
    assert branch == "cuttlefish/add-a-summary-1031-0905"
    assert Path(path) == repo / ".cuttlefish" / "worktrees" / "add-a-summary-1031-0905"
    assert _git(Path(path), "branch", "--show-current").strip() == branch
    assert _git(repo, "branch", "--show-current").strip() == here  # the checkout is untouched


def test_the_same_goal_in_the_same_minute_gets_a_suffix(repo: Path) -> None:
    first = worktree.create(str(repo), "Same", now=NOW)
    second = worktree.create(str(repo), "Same", now=NOW)
    assert second[0] == first[0] + "-2" and second[1] != first[1]


def test_a_stale_registration_and_a_leftover_folder_are_cleared(repo: Path) -> None:
    _, gone = worktree.create(str(repo), "Old", now=NOW)
    shutil.rmtree(gone)  # the folder vanished, git still lists it
    left = repo / ".cuttlefish" / "worktrees" / "again-1031-0905"
    left.mkdir(parents=True)
    (left / "junk").write_text("half-made")
    branch, path = worktree.create(str(repo), "Again", now=NOW)
    assert Path(path) == left and not (left / "junk").exists()
    assert gone not in _git(repo, "worktree", "list")
    assert branch in _git(repo, "branch", "--list")


def test_discard_removes_the_worktree_and_its_branch(repo: Path) -> None:
    branch, path = worktree.create(str(repo), "Never started", now=NOW)
    worktree.discard(str(repo), branch, path)
    assert not Path(path).exists() and branch not in _git(repo, "branch", "--list")


def test_a_folder_without_a_commit_or_repository_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(worktree.WorktreeError):
        worktree.create(str(tmp_path), "x")
    _git(tmp_path, "init", "-q")
    with pytest.raises(worktree.WorktreeError):
        worktree.create(str(tmp_path), "x")
