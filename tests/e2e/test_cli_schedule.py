"""End-to-end: `cuttlefish schedule add|list|remove|enable|disable`, in-process (CUT-246)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuttlefish import cli


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / "demo").mkdir()


def _project(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    cli.main(["projects", "add", "--name", "demo", "--root", str(tmp_path / "demo")])
    project_id: str = json.loads(capsys.readouterr().out)["id"]
    return project_id


def test_add_list_disable_enable_remove(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pid = _project(tmp_path, capsys)
    args = ["schedule", "add", pid, "--goal", "Triage issues", "--recurrence", "daily 06:30"]
    assert cli.main(args) == cli.EXIT_OK
    added = json.loads(capsys.readouterr().out)
    assert added["recurrence"] == "daily 06:30" and added["enabled"] is True

    assert cli.main(["schedule", "list", "--project", pid]) == cli.EXIT_OK
    assert [s["id"] for s in map(json.loads, capsys.readouterr().out.splitlines())] == [added["id"]]

    assert cli.main(["schedule", "disable", added["id"]]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["enabled"] is False
    assert cli.main(["schedule", "enable", added["id"]]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["enabled"] is True

    assert cli.main(["schedule", "remove", added["id"]]) == cli.EXIT_OK
    capsys.readouterr()
    cli.main(["schedule", "list"])
    assert capsys.readouterr().out == ""


def test_refusals_say_why(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pid = _project(tmp_path, capsys)
    bad = ["schedule", "add", pid, "--goal", "g", "--recurrence", "weekly"]
    assert cli.main(bad) == cli.EXIT_TASK_FAILED
    assert "unknown recurrence" in capsys.readouterr().err
    nope = ["schedule", "add", "nope", "--goal", "g", "--recurrence", "every 5m"]
    assert cli.main(nope) == cli.EXIT_TASK_FAILED
    assert "no project" in capsys.readouterr().err
    assert cli.main(["schedule", "remove", "nope"]) == cli.EXIT_TASK_FAILED
    assert "no schedule" in capsys.readouterr().err
