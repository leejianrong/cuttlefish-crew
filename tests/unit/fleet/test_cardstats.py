"""Unit: the diff stat and PR state on a project card (CUT-287). A background sweep fills a cache;
the project list only reads it, so no request ever runs git or gh."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import satay.control
from starlette.testclient import TestClient

from cuttlefish.fleet import cardstats
from cuttlefish.fleet.cardstats import CardStats
from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.prstatus import PrStatus
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.projects.store import ProjectStore
from cuttlefish.sessions.manager import write_goal


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True,
    )  # fmt: skip


def _repo(root: Path, *, goal: bool = True) -> Path:
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    (root / "a.txt").write_text("one\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "first")
    _git(root, "switch", "-q", "-c", "cuttlefish/work")
    (root / "b.txt").write_text("1\n2\n3\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "work")
    _git(root, "switch", "-q", "main")
    if goal:
        write_goal(str(root), "Work", "claude-code", branch="cuttlefish/work", worktree=str(root))
    return root


def _project(root: Path, pid: str, worker: str | None = None) -> Any:
    return SimpleNamespace(id=pid, root=str(root), worker_id=worker)


def test_a_sweep_reads_each_goal_branchs_counts_and_skips_the_rest(tmp_path: Path) -> None:
    with_goal = _repo(tmp_path / "a")
    no_goal = _repo(tmp_path / "b", goal=False)
    on_worker = _repo(tmp_path / "c")
    stats = CardStats(
        lambda: [
            _project(with_goal, "a"),
            _project(no_goal, "b"),
            _project(on_worker, "c", worker="w1"),
        ],
        lambda _p: None,
    )
    stats._sweep()
    assert stats.get("a") == {"f": 1, "a": 3, "d": 0}
    assert stats.get("b") is None and stats.get("c") is None


def test_pull_request_state_comes_only_for_a_goal_with_a_pr_and_is_read_rarely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path / "a")
    calls: list[str] = []

    def fake(_root: str, _goal: object) -> PrStatus:
        calls.append("gh")
        return PrStatus(True, state="open", checks_state="failing")

    monkeypatch.setattr(cardstats, "read_pr_status", fake)
    plain = CardStats(lambda: [_project(root, "a")], lambda _p: {"text": "g"})
    plain._sweep()
    assert calls == [] and "p" not in (plain.get("a") or {})
    stats = CardStats(
        lambda: [_project(root, "a")], lambda _p: {"text": "g", "pr_url": "https://x/1"}
    )
    stats._sweep()
    assert stats.get("a") == {"f": 1, "a": 3, "d": 0, "p": "o", "c": "f"}
    stats._sweep()
    assert calls == ["gh"]  # a second sweep within the PR window does not ask gh again


def test_a_flaky_gh_keeps_what_was_known(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path / "a")
    answers = iter(
        [PrStatus(True, state="open", checks_state="passing"), PrStatus(False, code="gh_failed")]
    )
    monkeypatch.setattr(cardstats, "read_pr_status", lambda *_: next(answers))
    stats = CardStats(
        lambda: [_project(root, "a")], lambda _p: {"text": "g", "pr_url": "https://x/1"}
    )
    stats._sweep()
    stats._prs["a"] = (-10_000.0, "open", "passing")  # as if the window had passed
    stats._sweep()
    assert stats.get("a") == {"f": 1, "a": 3, "d": 0, "p": "o", "c": "p"}


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = ProjectStore.open(tmp_path / "projects.db")
    app = create_app(FleetDaemon(store), security=satay.control.SecurityPolicy(token="t"))
    with TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "t"}) as c:
        c.store = store  # type: ignore[attr-defined]
        yield c
    store.close()


def test_the_list_never_waits_for_git_and_gets_the_stat_once_the_sweep_has_run(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path / "proj")
    pid = client.store.register(name="alpha", root=str(root)).id  # type: ignore[attr-defined]
    release, started = threading.Event(), threading.Event()
    real = cardstats.goal_stat

    def slow(*args: Any) -> Any:
        started.set()
        release.wait(10)  # a stat that takes "forever": the request must not wait for it
        return real(*args)

    monkeypatch.setattr(cardstats, "goal_stat", slow)
    first = client.get("/api/projects").json()["projects"][0]
    assert "gs" not in first  # nothing yet, and the request came back
    assert started.wait(5)  # the sweep did start, in the background
    release.set()
    for _ in range(100):
        listed = client.get("/api/projects").json()["projects"][0]
        if "gs" in listed:
            break
        threading.Event().wait(0.1)
    assert listed["gs"] == {"f": 1, "a": 3, "d": 0}
    assert listed["id"] == pid
