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


def test_remove_takes_the_worktree_and_keeps_the_branch(repo: Path) -> None:
    branch, path = worktree.create(str(repo), "Add a summary", now=NOW)
    result = worktree.remove(str(repo), path, branch)
    assert result.ok and not result.branch_deleted
    assert not Path(path).exists()
    assert branch in _git(repo, "branch", "--list")


def test_remove_refuses_uncommitted_and_untracked_changes_with_the_reason(repo: Path) -> None:
    branch, path = worktree.create(str(repo), "Add a summary", now=NOW)
    (Path(path) / "new.txt").write_text("work")
    result = worktree.remove(str(repo), path, branch)
    assert not result.ok and result.code == "uncommitted"
    assert result.reason and "uncommitted" in result.reason
    assert (Path(path) / "new.txt").exists()


def test_remove_deletes_a_merged_branch_on_request_and_keeps_an_unmerged_one(repo: Path) -> None:
    branch, path = worktree.create(str(repo), "Merged", now=NOW)
    assert worktree.remove(str(repo), path, branch, delete_branch=True).branch_deleted
    assert branch not in _git(repo, "branch", "--list")

    branch, path = worktree.create(str(repo), "Unmerged", now=NOW)
    (Path(path) / "a.txt").write_text("a")
    _git(Path(path), "add", "a.txt")
    _git(Path(path), "commit", "-q", "-m", "work")
    result = worktree.remove(str(repo), path, branch, delete_branch=True)
    assert result.ok and not result.branch_deleted and result.branch_note
    assert branch in _git(repo, "branch", "--list")


def test_remove_refuses_a_folder_that_is_not_a_goal_worktree(repo: Path) -> None:
    result = worktree.remove(str(repo), str(repo), None)
    assert not result.ok and result.code == "not_a_worktree"
    assert repo.exists()


def test_remove_of_a_folder_already_gone_still_clears_the_registration(repo: Path) -> None:
    branch, path = worktree.create(str(repo), "Gone", now=NOW)
    shutil.rmtree(path)
    assert worktree.remove(str(repo), path, branch).ok
    assert str(Path(path).resolve()) not in _git(repo, "worktree", "list")


def test_remove_refuses_a_plain_folder_git_does_not_list_and_leaves_it(repo: Path) -> None:
    folder = repo / ".cuttlefish" / "worktrees" / "plain"
    folder.mkdir(parents=True)
    result = worktree.remove(str(repo), str(folder), None)
    assert not result.ok and result.code == "not_a_worktree"
    assert folder.exists()


def test_remove_refuses_while_the_builder_runs_and_removes_nothing(repo: Path) -> None:
    branch, path = worktree.create(str(repo), "Busy", now=NOW)
    result = worktree.remove(str(repo), path, branch, delete_branch=True, builder_running=True)
    assert not result.ok and result.code == "running"
    assert Path(path).exists() and branch in _git(repo, "branch", "--list")


def test_an_unmerged_branch_note_is_a_sentence_not_git_output(repo: Path) -> None:
    branch, path = worktree.create(str(repo), "Note", now=NOW)
    (Path(path) / "a.txt").write_text("a")
    _git(Path(path), "add", "a.txt")
    _git(Path(path), "commit", "-q", "-m", "work")
    note = worktree.remove(str(repo), path, branch, delete_branch=True).branch_note
    assert note and "error:" not in note and "-D" not in note
