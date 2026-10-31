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


def test_leaves_a_folder_that_is_not_a_repository_alone(tmp_path: Path) -> None:
    assert ensure_excluded(tmp_path) is False  # no .git
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /elsewhere\n")  # a .git file naming no repository
    assert ensure_excluded(worktree) is False
    assert sorted(p.name for p in worktree.iterdir()) == [".git"]


def test_a_read_only_git_directory_is_skipped_not_raised(tmp_path: Path) -> None:
    exclude = _repo(tmp_path)
    exclude.mkdir()  # reading it as a file fails with an OSError
    assert ensure_excluded(tmp_path) is False


def _git(cwd: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_a_linked_worktree_is_excluded_in_the_shared_git_dir(tmp_path: Path) -> None:
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q")
    _git(main, "commit", "-q", "--allow-empty", "-m", "x")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "side", str(wt))
    assert (wt / ".git").is_file()
    assert ensure_excluded(wt) is True
    (wt / ".cuttlefish").mkdir()
    (wt / ".satay").mkdir()
    (wt / ".cuttlefish" / "j").write_text("x")
    assert _git(wt, "status", "--porcelain") == ""
    assert ensure_excluded(wt) is False
