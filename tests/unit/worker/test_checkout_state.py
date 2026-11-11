"""Unit: what a worker's checkout holds that the remote does not (CUT-207)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from cuttlefish.worker.execute import checkout, checkout_state


def _git(path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
    )


def _origin(tmp_path: Path) -> Path:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q")
    _git(origin, "commit", "-q", "--allow-empty", "-m", "first")
    return origin


def test_a_clone_with_nothing_of_its_own_reports_nothing(tmp_path: Path) -> None:
    work = checkout(tmp_path / "work", "p1", str(_origin(tmp_path)))
    (work / ".satay").mkdir()  # the team's own folder is not the project's change
    assert checkout_state(work) == {"dirty": 0, "unpushed": 0}


def test_uncommitted_files_and_a_commit_no_remote_has_are_counted(tmp_path: Path) -> None:
    work = checkout(tmp_path / "work", "p1", str(_origin(tmp_path)))
    _git(work, "switch", "-q", "-c", "agent/builder")
    (work / "a.txt").write_text("a")
    _git(work, "add", "a.txt")
    _git(work, "commit", "-q", "-m", "work")
    (work / "b.txt").write_text("b")
    assert checkout_state(work) == {"dirty": 1, "unpushed": 1}
