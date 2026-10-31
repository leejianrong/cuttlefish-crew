from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cuttlefish.sessions import role_trees
from cuttlefish.sessions.worktree import WorktreeError


def _git(cwd: Path | str, *args: str) -> str:
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
    (tmp_path / "shared.txt").write_text("base\n")
    _git(tmp_path, "add", "shared.txt")
    _git(tmp_path, "commit", "-q", "-m", "first")
    return tmp_path


def test_each_role_gets_its_own_worktree_and_the_checkout_is_untouched(repo: Path) -> None:
    here = _git(repo, "branch", "--show-current")
    trees = role_trees.prepare(str(repo), "t1", ["Dev", "Reviewer"])
    assert set(trees.roles) == {"Dev", "Reviewer"}
    paths = {t.path for t in trees.roles.values()} | {trees.integration.path}
    assert len(paths) == 3 and all(Path(p).is_dir() for p in paths)
    assert all(Path(p).parent == repo / ".cuttlefish" / "worktrees" for p in paths)
    assert _git(repo, "branch", "--show-current") == here


def test_roles_editing_different_files_merge_into_the_integration_branch(repo: Path) -> None:
    trees = role_trees.prepare(str(repo), "t1", ["dev", "docs"])
    (Path(trees.roles["dev"].path) / "code.py").write_text("x = 1\n")  # left uncommitted
    (Path(trees.roles["docs"].path) / "README.md").write_text("hi\n")
    results = role_trees.merge_back(trees)
    assert [r.status for r in results.values()] == ["merged", "merged"]
    assert (Path(trees.integration.path) / "code.py").exists()
    assert (Path(trees.integration.path) / "README.md").exists()
    assert not (repo / "code.py").exists()  # the checkout still has none of it
    role_trees.remove_merged(str(repo), trees, results)
    assert not Path(trees.roles["dev"].path).exists()
    assert trees.roles["dev"].branch not in _git(repo, "branch", "--list")


def test_a_role_that_changed_nothing_is_reported_as_such(repo: Path) -> None:
    trees = role_trees.prepare(str(repo), "t1", ["dev"])
    assert role_trees.merge_back(trees)["dev"].status == "nothing"


def test_a_conflict_is_aborted_and_reported_and_that_role_keeps_its_work(repo: Path) -> None:
    trees = role_trees.prepare(str(repo), "t1", ["a", "b", "c"])
    for role in ("a", "b"):
        (Path(trees.roles[role].path) / "shared.txt").write_text(f"{role}\n")
    (Path(trees.roles["c"].path) / "c.txt").write_text("c\n")
    results = role_trees.merge_back(trees)
    assert results["a"].status == "merged"
    assert results["b"].status == "conflict" and results["b"].files == ("shared.txt",)
    assert results["c"].status == "merged"  # a conflict does not stop the rest
    assert not _git(trees.integration.path, "status", "--porcelain").strip()  # aborted cleanly
    role_trees.remove_merged(str(repo), trees, results)
    assert Path(trees.roles["b"].path).exists()
    assert trees.roles["b"].branch in _git(repo, "branch", "--list")


def test_a_second_team_with_the_same_id_is_refused_without_damage(repo: Path) -> None:
    first = role_trees.prepare(str(repo), "t1", ["dev"])
    with pytest.raises(WorktreeError):
        role_trees.prepare(str(repo), "t1", ["dev"])
    assert Path(first.roles["dev"].path).is_dir()


def test_a_repository_with_no_commit_is_refused(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    with pytest.raises(WorktreeError):
        role_trees.prepare(str(tmp_path), "t1", ["dev"])
