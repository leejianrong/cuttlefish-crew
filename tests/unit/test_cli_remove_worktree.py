from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cuttlefish import cli
from cuttlefish.sessions import worktree
from cuttlefish.sessions.manager import write_goal


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True)


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str) -> tuple[Any, str, str]:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "first")
    branch, path = worktree.create(str(tmp_path), "Goal")
    write_goal(str(tmp_path), "Goal", "claude-code", branch=branch, worktree=path)
    store = SimpleNamespace(get=lambda _id: SimpleNamespace(id="p", root=str(tmp_path)))
    fake = SimpleNamespace(status=lambda _id, _root: SimpleNamespace(state=state))
    monkeypatch.setattr(cli, "SessionManager", lambda *a, **k: fake)
    return store, branch, path


def test_the_cli_refuses_while_the_builder_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store, branch, path = _setup(tmp_path, monkeypatch, "running")
    assert cli._project_remove_worktree(store, "p", delete_branch=True) != cli.EXIT_OK
    assert "still running" in capsys.readouterr().err
    assert Path(path).exists()
    out = subprocess.run(["git", "branch", "--list"], cwd=tmp_path, capture_output=True, text=True)
    assert branch in out.stdout


def test_the_cli_removes_once_the_builder_has_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _branch, path = _setup(tmp_path, monkeypatch, "stopped")
    assert cli._project_remove_worktree(store, "p", delete_branch=False) == cli.EXIT_OK
    assert not Path(path).exists()
