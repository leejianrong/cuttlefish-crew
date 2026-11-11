"""Sharing ignored directories and copying listed files into a goal worktree (CUT-296, ADR-0048)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cuttlefish.fleet import worktreeshare as share
from cuttlefish.sessions import worktree


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
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    (root / ".gitignore").write_text("node_modules/\n.env\n.env.local\nlocal/\n*.cache\n")
    (root / "src.txt").write_text("tracked")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "first")
    (root / "node_modules" / "left-pad").mkdir(parents=True)
    (root / "node_modules" / "left-pad" / "index.js").write_text("module.exports = 1")
    (root / ".env").write_text("OPENROUTER_API_KEY=real")
    (root / "local").mkdir()
    (root / "local" / "dev.json").write_text("{}")
    (root / "fixtures.cache").write_text("cached")
    return root


@pytest.fixture
def tree(repo: Path) -> Path:
    return Path(worktree.create(str(repo), "A goal")[1])


# -- the settings are checked when they are set --------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "why"),
    [
        ("/etc", "relative"),
        ("../elsewhere", "outside"),
        ("a/../../b", "outside"),
        (".", "project"),
        ("", "empty"),
        ("a\x00b", "invalid"),
        ("node_*", "pattern"),
        (".git", "reserved"),
        (".git/hooks", "reserved"),
        (".cuttlefish/worktrees", "reserved"),
        (".satay", "reserved"),
        (".env", "secret"),
        (".env.local", "secret"),
        ("keys/server.pem", "secret"),
        ("id_ed25519", "secret"),
        (".npmrc", "secret"),
        ("secrets.json", "secret"),
        (".ssh", "reserved"),
        ("x" * 300, "long"),
    ],
)
def test_a_path_that_is_outside_the_project_or_a_secret_is_refused(
    repo: Path, entry: str, why: str
) -> None:
    for kind in (share.validate_dirs, share.validate_files):
        with pytest.raises(share.ShareError):
            kind(str(repo), [entry])


def test_valid_entries_are_normalised_and_deduplicated(repo: Path) -> None:
    assert share.validate_dirs(str(repo), ["./node_modules/", "node_modules", "local"]) == (
        "node_modules",
        "local",
    )
    assert share.validate_files(str(repo), ["./fixtures.cache"]) == ("fixtures.cache",)
    assert share.validate_files(str(repo), [".env.example"]) == (".env.example",)  # a template


def test_an_existing_path_that_resolves_outside_the_project_is_refused(
    repo: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "escape").symlink_to(outside)
    with pytest.raises(share.ShareError, match="outside"):
        share.validate_dirs(str(repo), ["escape"])


def test_there_is_a_limit_on_how_many(repo: Path) -> None:
    with pytest.raises(share.ShareError, match="at most"):
        share.validate_dirs(str(repo), [f"d{i}" for i in range(share.MAX_ENTRIES + 1)])


def test_a_non_list_is_refused(repo: Path) -> None:
    with pytest.raises(share.ShareError):
        share.validate_dirs(str(repo), "node_modules")  # type: ignore[arg-type]


# -- what happens in a new worktree --------------------------------------------------------------


def test_nothing_is_shared_by_default(repo: Path, tree: Path) -> None:
    report = share.apply(str(repo), str(tree), (), ())
    assert report == share.Report()
    assert not (tree / "node_modules").exists() and not (tree / "fixtures.cache").exists()


def test_a_listed_ignored_directory_is_linked_and_hidden_from_git(repo: Path, tree: Path) -> None:
    report = share.apply(str(repo), str(tree), ("node_modules",), ())
    assert report.linked == ["node_modules"] and report.skipped == []
    link = tree / "node_modules"
    assert link.is_symlink() and link.resolve() == (repo / "node_modules").resolve()
    assert (link / "left-pad" / "index.js").read_text() == "module.exports = 1"
    # the symlink does not show up as an untracked file a `git add -A` would commit
    assert _git(tree, "status", "--porcelain").strip() == ""


def test_a_listed_ignored_file_is_copied_not_linked(repo: Path, tree: Path) -> None:
    report = share.apply(str(repo), str(tree), (), ("fixtures.cache", "local/dev.json"))
    assert sorted(report.copied) == ["fixtures.cache", "local/dev.json"]
    copied = tree / "fixtures.cache"
    assert copied.is_file() and not copied.is_symlink()
    copied.write_text("changed in the goal")
    assert (repo / "fixtures.cache").read_text() == "cached"  # the original is untouched
    assert _git(tree, "status", "--porcelain").strip() == ""  # ignored, so not a change


def test_a_path_git_does_not_ignore_is_skipped_so_it_cannot_land_in_a_commit(
    repo: Path, tree: Path
) -> None:
    (repo / "notes").mkdir()
    (repo / "notes" / "todo.txt").write_text("x")
    (repo / "scratch.txt").write_text("x")
    report = share.apply(str(repo), str(tree), ("notes",), ("scratch.txt",))
    assert report.linked == [] and report.copied == []
    assert {p for p, _ in report.skipped} == {"notes", "scratch.txt"}
    assert all("ignore" in why for _, why in report.skipped)
    assert not (tree / "notes").exists() and not (tree / "scratch.txt").exists()


def test_a_secret_file_is_never_copied_even_if_it_is_listed_behind_the_settings(
    repo: Path, tree: Path
) -> None:
    """The settings refuse it; `apply` refuses it again, for a row written some other way."""
    report = share.apply(str(repo), str(tree), (), (".env",))
    assert report.copied == [] and report.skipped == [(".env", "it looks like a secret")]
    assert not (tree / ".env").exists()


def test_a_path_that_escapes_the_project_by_a_symlink_is_skipped(
    repo: Path, tree: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.cache").write_text("secretish")
    (repo / "link.cache").symlink_to(outside / "x.cache")
    (repo / "local" / "out").symlink_to(outside)
    report = share.apply(str(repo), str(tree), ("local/out",), ("link.cache",))
    assert report.linked == [] and report.copied == []
    assert {p for p, _ in report.skipped} == {"local/out", "link.cache"}
    assert not (tree / "link.cache").exists()


def test_something_already_in_the_worktree_is_left_alone(repo: Path, tree: Path) -> None:
    (tree / "fixtures.cache").write_text("mine")
    report = share.apply(str(repo), str(tree), (), ("fixtures.cache",))
    assert report.copied == [] and report.skipped == [("fixtures.cache", "it is already there")]
    assert (tree / "fixtures.cache").read_text() == "mine"


def test_a_missing_path_and_an_oversized_file_are_skipped_not_fatal(repo: Path, tree: Path) -> None:
    (repo / "big.cache").write_bytes(b"x" * (share.MAX_COPY_BYTES + 1))
    report = share.apply(str(repo), str(tree), ("absent",), ("big.cache", "fixtures.cache"))
    assert report.copied == ["fixtures.cache"]
    assert dict(report.skipped)["absent"] == "it is not in the project"
    assert "larger" in dict(report.skipped)["big.cache"]


def test_the_wrong_kind_is_skipped(repo: Path, tree: Path) -> None:
    report = share.apply(str(repo), str(tree), ("fixtures.cache",), ("local",))
    assert report.linked == [] and report.copied == []
    assert {p for p, _ in report.skipped} == {"fixtures.cache", "local"}


def test_the_worktree_must_be_inside_the_project(repo: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(share.ShareError, match="inside"):
        share.apply(str(repo), str(elsewhere), ("node_modules",), ())
    assert not os.path.lexists(elsewhere / "node_modules")


def test_the_report_reads_as_json(repo: Path, tree: Path) -> None:
    report = share.apply(str(repo), str(tree), ("node_modules",), ("fixtures.cache", "scratch"))
    assert report.to_json() == {
        "linked": ["node_modules"],
        "copied": ["fixtures.cache"],
        "skipped": [{"path": "scratch", "why": "it is not in the project"}],
    }
