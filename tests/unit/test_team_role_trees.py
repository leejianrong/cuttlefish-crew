"""A git project's team gives each kopicode role its own worktree and merges them (CUT-239,
ADR-0054). Real satay, real journal, real git repositories, a scripted ``kopicode run --print`` that
writes the file its role's task names, in the folder it was started in."""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import satay
from satay.journal.store import SQLiteStore
from satay.testing.faults import FaultInjector, SimulatedCrash

from cuttlefish import runtime
from cuttlefish.episodic.events import RoleMergeReported, TeamBranchMade
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.sessions import role_trees
from cuttlefish.team import run_team

#: Writes ``<file>`` with ``<text>`` where the task says ``WRITE <file> <text>``, in its cwd.
_FAKE = """#!{python}
import json, os, sys, time
with open(os.environ["FAKE_KOPICODE_SPANS"], "a") as spans:
    spans.write(f"start {{time.time()}}\\n")
time.sleep(float(os.environ.get("FAKE_KOPICODE_SLEEP", "0")))
task = sys.argv[-1]
words = task.split("WRITE ", 1)[1].split()
with open(os.path.join(os.getcwd(), words[0]), "w") as f:
    f.write(" ".join(words[1:]) + "\\n")
with open(os.environ["FAKE_KOPICODE_CWDS"], "a") as log:
    log.write(os.getcwd() + "\\n")
with open(os.environ["FAKE_KOPICODE_SPANS"], "a") as spans:
    spans.write(f"end {{time.time()}}\\n")
print(json.dumps({{"kind": "session_started", "detail": "s"}}))
print(json.dumps({{"kind": "edit_applied", "path": words[0]}}))
print(json.dumps({{"kind": "session_ended", "reason": "completed", "exit_code": 0}}))
"""


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
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "shared.txt").write_text("base\n")
    _git(root, "add", "shared.txt")
    _git(root, "commit", "-q", "-m", "first")
    return root


@pytest.fixture(autouse=True)
def _fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    binary = tmp_path / "kopicode"
    binary.write_text(_FAKE.format(python=sys.executable))
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("CUTTLEFISH_KOPICODE_TRANSPORT", "print")
    monkeypatch.setenv("FAKE_KOPICODE_CWDS", str(tmp_path / "cwds.log"))
    monkeypatch.setenv("FAKE_KOPICODE_SPANS", str(tmp_path / "spans.log"))
    return binary


def _team(root: Path, **work: str) -> dict[str, Any]:
    return {
        "team_id": "t1",
        "root": str(root),
        "roles": [{"name": n, "text": f"WRITE {w}"} for n, w in work.items()],
    }


async def _run(
    tmp_path: Path, team: dict[str, Any], **start: Any
) -> tuple[dict[str, Any], list[Any]]:
    store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(episodic_store=store, kopicode_binary=str(tmp_path / "kopicode"))
    )
    result = await satay.start(
        run_team, team, run_id="t1", store=SQLiteStore.open(":memory:"), **start
    ).result()
    payloads = [e.payload for e in store.read("t1")]
    store.close()
    return result, payloads


async def test_two_roles_run_in_their_own_worktrees_and_merge_into_a_team_branch(
    tmp_path: Path, repo: Path
) -> None:
    here = _git(repo, "branch", "--show-current")
    result, payloads = await _run(tmp_path, _team(repo, dev="dev.py x=1", docs="README.md hello"))

    assert result["status"] == "completed"
    made = next(p for p in payloads if isinstance(p, TeamBranchMade))
    assert made.branch == "cuttlefish/team-t1"
    assert made.roles == {"dev": "cuttlefish/team-t1-dev", "docs": "cuttlefish/team-t1-docs"}
    merges = {p.role: p.status for p in payloads if isinstance(p, RoleMergeReported)}
    assert merges == {"dev": "merged", "docs": "merged"}
    # Each agent ran in its role's folder, never the checkout.
    cwds = {Path(c).name for c in (tmp_path / "cwds.log").read_text().split()}
    assert cwds == {"team-t1-dev", "team-t1-docs"}
    # The work is on the team branch; the checkout and its branch are untouched.
    shown = _git(repo, "ls-tree", "--name-only", "cuttlefish/team-t1").split()
    assert {"dev.py", "README.md"} <= set(shown)
    assert not (repo / "dev.py").exists()
    assert _git(repo, "branch", "--show-current") == here
    # Merged roles' branches are gone; the team branch stays for the person.
    branches = _git(repo, "branch", "--list", "cuttlefish/*", "--format=%(refname:short)").split()
    assert branches == ["cuttlefish/team-t1"]


async def test_a_conflict_is_reported_and_aborted_never_resolved(
    tmp_path: Path, repo: Path
) -> None:
    result, payloads = await _run(
        tmp_path, _team(repo, dev="shared.txt from-dev", docs="shared.txt from-docs")
    )

    assert result["status"] == "completed"
    merges = {p.role: p for p in payloads if isinstance(p, RoleMergeReported)}
    assert merges["dev"].status == "merged"
    assert merges["docs"].status == "conflict" and merges["docs"].files == ["shared.txt"]
    team_tree = repo / ".cuttlefish" / "worktrees" / "team-t1"
    assert (team_tree / "shared.txt").read_text() == "from-dev\n"  # no conflict markers
    assert _git(team_tree, "status", "--porcelain") == ""
    # The conflicted role's branch and worktree keep its work.
    assert _git(repo, "show", "cuttlefish/team-t1-docs:shared.txt") == "from-docs\n"
    assert (repo / ".cuttlefish" / "worktrees" / "team-t1-docs").is_dir()
    assert not (repo / ".cuttlefish" / "worktrees" / "team-t1-dev").exists()


async def test_a_non_git_root_and_a_single_kopicode_role_are_unchanged(
    tmp_path: Path, repo: Path
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    result, payloads = await _run(tmp_path, _team(plain, dev="a.txt 1", docs="b.txt 2"))
    assert result["status"] == "completed"
    assert (plain / "a.txt").exists() and (plain / "b.txt").exists()
    assert not [p for p in payloads if isinstance(p, TeamBranchMade | RoleMergeReported)]

    one = tmp_path / "one.db"
    one.unlink(missing_ok=True)
    (tmp_path / "episodic.db").unlink()
    result, payloads = await _run(tmp_path, _team(repo, dev="a.txt 1"))
    assert (repo / "a.txt").exists()
    assert not [p for p in payloads if isinstance(p, TeamBranchMade)]


async def test_trees_a_crash_left_are_reused_with_their_work(tmp_path: Path, repo: Path) -> None:
    # A run killed after making its worktrees but before journaling them: the rerun must not fail
    # on "already exists", and must keep what a role had already done.
    trees = role_trees.prepare(str(repo), "t1", ["dev", "docs"])
    (Path(trees.roles["dev"].path) / "early.py").write_text("kept\n")

    result, _ = await _run(tmp_path, _team(repo, dev="dev.py x=1", docs="README.md hello"))

    assert result["status"] == "completed"
    shown = set(_git(repo, "ls-tree", "--name-only", "cuttlefish/team-t1").split())
    assert {"early.py", "dev.py", "README.md"} <= shown


async def test_a_killed_run_resumes_without_making_the_worktrees_twice(
    tmp_path: Path, repo: Path
) -> None:
    store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(episodic_store=store, kopicode_binary=str(tmp_path / "kopicode"))
    )
    satay_store = SQLiteStore.open(":memory:")
    team = _team(repo, dev="dev.py x=1", docs="README.md hello")
    injector = FaultInjector()
    injector.crash_after("TaskCompleted")
    with pytest.raises(SimulatedCrash):
        await satay.start(
            run_team, team, run_id="t1", store=satay_store, injector=injector
        ).result()

    result = await satay.start(run_team, team, run_id="t1", store=satay_store).result()

    assert result["status"] == "completed"
    payloads = [e.payload for e in store.read("t1")]
    assert len([p for p in payloads if isinstance(p, TeamBranchMade)]) == 1
    assert len([p for p in payloads if isinstance(p, RoleMergeReported)]) == 2
    store.close()


def _overlapped(tmp_path: Path) -> bool:
    """Whether a second agent started before the first one ended."""
    events = [line.split()[0] for line in (tmp_path / "spans.log").read_text().splitlines()]
    return events[:2] == ["start", "start"]


async def test_roles_in_a_git_project_run_at_the_same_time(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_KOPICODE_SLEEP", "0.5")
    result, _ = await _run(tmp_path, _team(repo, dev="dev.py x=1", docs="README.md hello"))
    assert result["status"] == "completed"
    assert _overlapped(tmp_path)


async def test_roles_in_a_folder_that_is_not_a_repository_still_run_one_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_KOPICODE_SLEEP", "0.2")
    plain = tmp_path / "plain"
    plain.mkdir()
    result, _ = await _run(tmp_path, _team(plain, dev="a.txt 1", docs="b.txt 2"))
    assert result["status"] == "completed"
    assert not _overlapped(tmp_path)
