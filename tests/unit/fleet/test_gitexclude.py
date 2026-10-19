from __future__ import annotations

from pathlib import Path

from cuttlefish.fleet.gitexclude import ENTRIES, ensure_excluded


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".git" / "info").mkdir(parents=True)
    return tmp_path / ".git" / "info" / "exclude"


def test_adds_both_folders_to_a_repository_s_exclude_file(tmp_path: Path) -> None:
    exclude = _repo(tmp_path)
    exclude.write_text("# git's own header\n*.swp\n")
    assert ensure_excluded(tmp_path) is True
    lines = exclude.read_text().splitlines()
    assert lines[:2] == ["# git's own header", "*.swp"]  # what was there is kept
    assert ".cuttlefish/" in lines and ".satay/" in lines


def test_is_idempotent_and_adds_only_what_is_missing(tmp_path: Path) -> None:
    exclude = _repo(tmp_path)
    exclude.write_text(
        ".satay\nnotes.txt"
    )  # no trailing newline, one already there without a slash
    assert ensure_excluded(tmp_path) is True
    assert exclude.read_text().count(".satay") == 1
    assert ".cuttlefish/" in exclude.read_text().splitlines()
    assert "notes.txt" in exclude.read_text().splitlines()
    before = exclude.read_text()
    assert ensure_excluded(tmp_path) is False
    assert exclude.read_text() == before


def test_creates_the_file_when_git_has_none(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    assert ensure_excluded(tmp_path) is True
    assert (tmp_path / ".git" / "info" / "exclude").read_text().splitlines()[1:] == list(ENTRIES)


def test_leaves_a_folder_that_is_not_a_plain_repository_alone(tmp_path: Path) -> None:
    assert ensure_excluded(tmp_path) is False  # no .git
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /elsewhere\n")  # a linked worktree: .git is a file
    assert ensure_excluded(worktree) is False
    assert sorted(p.name for p in worktree.iterdir()) == [".git"]


def test_a_read_only_git_directory_is_skipped_not_raised(tmp_path: Path) -> None:
    exclude = _repo(tmp_path)
    exclude.mkdir()  # reading it as a file fails with an OSError
    assert ensure_excluded(tmp_path) is False
