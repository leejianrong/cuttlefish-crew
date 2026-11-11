"""Unit: a goal branch's diff read from git (CUT-289, ADR-0048), against real scratch repos."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import satay.control
from starlette.testclient import TestClient

from cuttlefish.fleet import goaldiff
from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.goaldiff import goal_diff, goal_stat, looks_secret
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.fleet.tiers import Tier, required_tier
from cuttlefish.projects.store import ProjectStore
from cuttlefish.sessions import worktree
from cuttlefish.sessions.manager import write_goal


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True,
    )  # fmt: skip


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A repo on main with a goal worktree holding one committed change."""
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n")
    (tmp_path / "old.txt").write_text("x\n" * 20)
    (tmp_path / "gone.txt").write_text("bye\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "first")
    branch, path = worktree.create(str(tmp_path), "Goal")
    wt = Path(path)
    (wt / "a.txt").write_text("one\nTWO\nthree\nfour\n")
    (wt / "gone.txt").unlink()
    (wt / "old.txt").rename(wt / "new.txt")
    (wt / "blob.bin").write_bytes(b"\0\1\2")
    _git(wt, "add", ".")
    _git(wt, "commit", "-q", "-m", "work")
    goal = write_goal(str(tmp_path), "Goal", "claude-code", branch=branch, worktree=path)
    return tmp_path, goal


def _by_path(diff: goaldiff.GoalDiff) -> dict[str, dict[str, object]]:
    assert diff.ok, diff.reason
    return {str(f["path"]): dict(f) for f in diff.files}


def test_files_hunks_renames_deletes_and_binaries(repo: tuple[Path, dict[str, str]]) -> None:
    root, goal = repo
    diff = goal_diff(str(root), goal)
    files = _by_path(diff)
    assert (diff.base, diff.mode, diff.dirty) == ("main", "worktree", False)
    a = files["a.txt"]
    assert (a["status"], a["additions"], a["deletions"]) == ("modified", 2, 1)
    lines = a["hunks"][0]["lines"]  # type: ignore[index]
    assert [(ln["type"], ln["text"]) for ln in lines] == [
        (" ", "one"), ("-", "two"), ("+", "TWO"), (" ", "three"), ("+", "four"),
    ]  # fmt: skip
    assert lines[1]["old"] == 2 and lines[2]["new"] == 2 and "new" not in lines[1]
    assert files["gone.txt"]["status"] == "deleted"
    assert files["new.txt"]["status"] == "renamed" and files["new.txt"]["old_path"] == "old.txt"
    assert files["blob.bin"]["binary"] is True and files["blob.bin"]["hunks"] == []
    assert diff.deletions == 2


def test_uncommitted_and_untracked_work_is_shown_and_ignored_work_is_not(
    repo: tuple[Path, dict[str, str]],
) -> None:
    root, goal = repo
    wt = Path(goal["worktree"])
    (wt / "a.txt").write_text("changed\n")
    (wt / "fresh.py").write_text("print(1)\nprint(2)\n")
    (wt / ".gitignore").write_text("junk.log\n")
    (wt / "junk.log").write_text("noise\n")
    diff = goal_diff(str(root), goal)
    files = _by_path(diff)
    assert diff.dirty and files["fresh.py"]["status"] == "untracked"
    assert files["fresh.py"]["additions"] == 2
    assert "junk.log" not in files
    assert files["a.txt"]["additions"] == 1


def test_secret_looking_and_ignored_files_show_no_lines(repo: tuple[Path, dict[str, str]]) -> None:
    root, goal = repo
    wt = Path(goal["worktree"])
    (wt / ".env").write_text("KEY=hunter2\n")
    (wt / "deploy.pem").write_text("-----BEGIN-----\n")
    (wt / ".env.example").write_text("KEY=\n")
    (wt / "tracked.cfg").write_text("secret=1\n")
    (wt / ".gitignore").write_text("tracked.cfg\n")
    _git(wt, "add", "-f", ".env", "tracked.cfg", ".gitignore")
    _git(wt, "commit", "-q", "-m", "oops")
    files = _by_path(goal_diff(str(root), goal))
    for name in (".env", "deploy.pem", "tracked.cfg"):
        assert files[name]["withheld"] == "secret" and files[name]["hunks"] == [], name
    assert files[".env.example"].get("withheld") is None
    assert "hunter2" not in repr(files)
    assert looks_secret("a/.cuttlefish/x.json") and not looks_secret("src/keys.py")


def test_a_huge_diff_is_cut_and_says_so(
    repo: tuple[Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, goal = repo
    wt = Path(goal["worktree"])
    (wt / "big1.txt").write_text("".join(f"line {n}\n" for n in range(3000)))
    (wt / "big2.txt").write_text("".join(f"line {n}\n" for n in range(3000)))
    _git(wt, "add", ".")
    _git(wt, "commit", "-q", "-m", "big")
    monkeypatch.setattr(goaldiff, "MAX_PATCH_BYTES", 20_000)
    diff = goal_diff(str(root), goal)
    files = _by_path(diff)
    assert diff.truncated and diff.note and "part of this diff" in diff.note
    assert files["big1.txt"]["additions"] == 3000  # counts stay true
    assert files["big2.txt"].get("omitted") is True


def test_one_long_file_is_cut_to_the_line_cap(repo: tuple[Path, dict[str, str]]) -> None:
    root, goal = repo
    wt = Path(goal["worktree"])
    (wt / "long.txt").write_text("".join(f"l{n}\n" for n in range(goaldiff.MAX_FILE_LINES + 50)))
    _git(wt, "add", ".")
    _git(wt, "commit", "-q", "-m", "long")
    entry = _by_path(goal_diff(str(root), goal))["long.txt"]
    assert entry["truncated"] is True
    assert len(entry["hunks"][0]["lines"]) == goaldiff.MAX_FILE_LINES  # type: ignore[index]


def test_a_removed_worktree_still_shows_the_branch(repo: tuple[Path, dict[str, str]]) -> None:
    root, goal = repo
    _git(root, "worktree", "remove", "--force", goal["worktree"])
    diff = goal_diff(str(root), {k: v for k, v in goal.items() if k != "worktree"})
    assert diff.ok and diff.mode == "branch" and "a.txt" in _by_path(diff)


def test_refusals_are_plain_and_never_raise(repo: tuple[Path, dict[str, str]]) -> None:
    root, goal = repo
    assert goal_diff(str(root), None).code == "no_goal"
    shared = goal_diff(str(root), {"text": "g", "at": "x"})  # no branch: the checkout, on main
    assert shared.code == "default_branch" and "Its own branch" in (shared.reason or "")
    gone = goal_diff(str(root), {**goal, "branch": "cuttlefish/nope", "worktree": ""})
    assert gone.code == "no_branch"
    assert goal_diff(str(root / "missing"), goal).ok is False


def test_a_branch_name_that_looks_like_a_flag_is_never_run_as_one(
    repo: tuple[Path, dict[str, str]], tmp_path_factory: pytest.TempPathFactory
) -> None:
    root, goal = repo
    target = tmp_path_factory.mktemp("out") / "pwned"
    out = goal_diff(str(root), {**goal, "branch": f"--output={target}", "worktree": ""})
    assert not out.ok and not target.exists()


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = ProjectStore.open(tmp_path / "projects.db")
    app = create_app(FleetDaemon(store), security=satay.control.SecurityPolicy(token="t"))
    with TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "t"}) as c:
        c.store = store  # type: ignore[attr-defined]
        yield c
    store.close()


def test_the_route_serves_the_diff_at_view_tier(
    client: TestClient, repo: tuple[Path, dict[str, str]]
) -> None:
    root, _goal = repo
    pid = client.store.register(name="alpha", root=str(root)).id  # type: ignore[attr-defined]
    body = client.get(f"/api/projects/{pid}/goal/diff").json()
    assert body["ok"] and body["branch"].startswith("cuttlefish/")
    assert {f["path"] for f in body["files"]} >= {"a.txt", "new.txt"}
    assert required_tier("GET", f"/api/projects/{pid}/goal/diff") == Tier.VIEW
    assert client.get("/api/projects/nope/goal/diff").status_code == 404


def test_a_project_on_a_worker_says_so(client: TestClient, tmp_path: Path) -> None:
    store = client.store  # type: ignore[attr-defined]
    pid = store.register(name="remote", root=str(tmp_path / "elsewhere")).id
    store.update_worker(pid, "w1")
    body = client.get(f"/api/projects/{pid}/goal/diff").json()
    assert body["ok"] is False and body["code"] == "on_worker" and "remote" in body["reason"]


def test_the_stat_is_the_diffs_counts_without_the_patch(repo: tuple[Path, dict[str, str]]) -> None:
    root, goal = repo
    wt = Path(goal["worktree"])
    (wt / "fresh.py").write_text("print(1)\nprint(2)\n")
    (wt / "a.txt").write_text("changed\n")
    stat, full = goal_stat(str(root), goal), goal_diff(str(root), goal)
    assert stat.ok and stat.files == () and stat.dirty
    assert (stat.files_changed, stat.additions, stat.deletions) == (
        full.files_changed, full.additions, full.deletions,
    )  # fmt: skip
    assert stat.files_changed == len(full.files) and stat.head == full.head
    assert goal_stat(str(root), None).code == "no_goal"
    assert goal_stat(str(root), {"text": "g", "at": "x"}).code == "default_branch"


def test_the_stat_route_and_its_worker_answer(
    client: TestClient, repo: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    root, _goal = repo
    store = client.store  # type: ignore[attr-defined]
    pid = store.register(name="alpha", root=str(root)).id
    body = client.get(f"/api/projects/{pid}/goal/diff/stat").json()
    assert body["ok"] and body["files_changed"] >= 4 and not body.get("files")
    assert required_tier("GET", f"/api/projects/{pid}/goal/diff/stat") == Tier.VIEW
    remote = store.register(name="remote", root=str(tmp_path / "x")).id
    store.update_worker(remote, "w1")
    assert client.get(f"/api/projects/{remote}/goal/diff/stat").json()["code"] == "on_worker"
