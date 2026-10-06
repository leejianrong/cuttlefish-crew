"""Unit: the folder picker's browser (cuttlefish.fleet.fs, ADR-0026) -- a real tmp tree."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cuttlefish.fleet.fs import (
    MAX_ENTRIES,
    FolderBrowser,
    NotAFolderError,
    OutsideBrowseRootsError,
)


def _git(path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=path,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    (root / "alpha").mkdir(parents=True)
    (root / "Beta").mkdir()
    (root / "gamma" / "inner").mkdir(parents=True)
    (root / ".hidden").mkdir()
    (root / "file.txt").write_text("x")
    (root / "alpha" / ".git").mkdir()
    return root


def test_lists_only_folders_sorted_without_case_and_skips_hidden_and_files(tree: Path) -> None:
    listing = FolderBrowser([tree]).list_folders()
    assert [f.name for f in listing.folders] == ["alpha", "Beta", "gamma"]
    assert listing.root == str(tree.resolve())
    assert listing.parent is None


def test_marks_git_folders(tree: Path) -> None:
    folders = {f.name: f for f in FolderBrowser([tree]).list_folders().folders}
    assert folders["alpha"].is_git is True
    assert folders["Beta"].is_git is False


def test_a_subfolder_has_a_parent_and_lists_its_own_folders(tree: Path) -> None:
    listing = FolderBrowser([tree]).list_folders(str(tree / "gamma"))
    assert [f.name for f in listing.folders] == ["inner"]
    assert listing.parent == str(tree.resolve())


def test_the_default_listing_is_the_first_root(tree: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    assert FolderBrowser([tree, other]).list_folders().path == str(tree.resolve())


def test_a_second_root_is_browsable(tree: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    (other / "proj").mkdir(parents=True)
    listing = FolderBrowser([tree, other]).list_folders(str(other))
    assert [f.name for f in listing.folders] == ["proj"]


@pytest.mark.parametrize("requested", ["/", "/etc", ".."])
def test_a_path_outside_every_root_is_refused(tree: Path, requested: str) -> None:
    with pytest.raises(OutsideBrowseRootsError):
        FolderBrowser([tree]).list_folders(requested if requested != ".." else str(tree / ".."))


def test_dot_dot_traversal_out_of_the_root_is_refused(tree: Path) -> None:
    with pytest.raises(OutsideBrowseRootsError):
        FolderBrowser([tree]).list_folders(str(tree / "alpha" / ".." / ".."))


def test_a_hidden_folder_cannot_be_listed_even_when_asked_for_by_name(tree: Path) -> None:
    with pytest.raises(OutsideBrowseRootsError):
        FolderBrowser([tree]).list_folders(str(tree / ".hidden"))


def test_a_missing_path_and_a_file_are_not_folders(tree: Path) -> None:
    browser = FolderBrowser([tree])
    with pytest.raises(NotAFolderError):
        browser.list_folders(str(tree / "nope"))
    with pytest.raises(NotAFolderError):
        browser.list_folders(str(tree / "file.txt"))


def test_a_symlink_to_a_folder_inside_the_tree_is_listed(tree: Path) -> None:
    (tree / "shortcut").symlink_to(tree / "gamma", target_is_directory=True)
    names = [f.name for f in FolderBrowser([tree]).list_folders().folders]
    assert "shortcut" in names


def test_a_symlink_out_of_the_tree_is_invisible_and_cannot_be_followed(
    tree: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    (outside / "secret").mkdir(parents=True)
    (tree / "escape").symlink_to(outside, target_is_directory=True)
    browser = FolderBrowser([tree])
    assert "escape" not in [f.name for f in browser.list_folders().folders]
    with pytest.raises(OutsideBrowseRootsError):
        browser.list_folders(str(tree / "escape"))


def test_a_symlink_to_a_file_is_not_a_folder(tree: Path) -> None:
    (tree / "link").symlink_to(tree / "file.txt")
    assert "link" not in [f.name for f in FolderBrowser([tree]).list_folders().folders]


def test_a_huge_listing_is_truncated_and_says_so(tmp_path: Path) -> None:
    root = tmp_path / "many"
    root.mkdir()
    for i in range(MAX_ENTRIES + 5):
        (root / f"d{i:04d}").mkdir()
    listing = FolderBrowser([root]).list_folders()
    assert len(listing.folders) == MAX_ENTRIES
    assert listing.truncated is True


def test_a_browser_needs_a_root() -> None:
    with pytest.raises(ValueError):
        FolderBrowser([])


def test_inspect_a_plain_folder_reports_languages_and_no_git(tree: Path) -> None:
    (tree / "Beta" / "pyproject.toml").write_text("")
    (tree / "Beta" / "package.json").write_text("{}")
    found = FolderBrowser([tree]).inspect(str(tree / "Beta"))
    assert found.name == "Beta"
    assert found.is_git is False
    assert found.branch is None and found.dirty is None and found.last_commit is None
    assert found.languages == ("Python", "JavaScript")


def test_inspect_a_clean_and_then_dirty_git_repo(tmp_path: Path) -> None:
    root = tmp_path / "home"
    repo = root / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "go.mod").write_text("module x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    browser = FolderBrowser([root])

    clean = browser.inspect(str(repo))
    assert (clean.is_git, clean.branch, clean.dirty) == (True, "main", False)
    assert clean.last_commit is not None
    assert clean.languages == ("Go",)

    (repo / "new.txt").write_text("x")
    assert browser.inspect(str(repo)).dirty is True


def test_inspect_refuses_outside_the_roots(tree: Path) -> None:
    with pytest.raises(OutsideBrowseRootsError):
        FolderBrowser([tree]).inspect("/etc")


def test_inspect_a_repo_with_no_commits_still_names_its_branch(tmp_path: Path) -> None:
    root = tmp_path / "home"
    repo = root / "fresh"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "trunk")
    found = FolderBrowser([root]).inspect(str(repo))
    assert (found.is_git, found.branch, found.last_commit) == (True, "trunk", None)


def test_inspect_a_detached_head_names_the_short_commit(tmp_path: Path) -> None:
    root = tmp_path / "home"
    repo = root / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    _git(repo, "checkout", "-q", "--detach")
    branch = FolderBrowser([root]).inspect(str(repo)).branch
    assert branch is not None and branch != "main" and len(branch) >= 7
