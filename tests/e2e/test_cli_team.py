"""End-to-end: `cuttlefish run-team`, in-process, against the real kopicode binary
where marked (ADR-0007) -- the same style tests/e2e/test_cli.py already uses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuttlefish import cli, runtime


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    yield
    runtime.reset()


@pytest.mark.requires_kopicode
def test_run_team_reaches_a_terminal_state_with_both_roles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    exit_code = cli.main(
        [
            "run-team",
            "--role",
            "builder:add a .gitignore entry",
            "--role",
            "reviewer:check the .gitignore entry",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_TASK_FAILED
    assert result["status"] == "failed"
    assert set(result["roles"]) == {"builder", "reviewer"}
    assert "team_id" in result


@pytest.mark.requires_kopicode
def test_show_on_a_team_id_renders_both_roles_interleaved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    cli.main(["run-team", "--role", "builder:add a .gitignore entry"])
    team_id = json.loads(capsys.readouterr().out)["team_id"]

    exit_code = cli.main(["show", team_id])
    output = capsys.readouterr().out

    assert exit_code == cli.EXIT_OK
    assert "role='builder'" in output


def test_run_team_with_no_roles_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["run-team"])

    assert exit_code == cli.EXIT_CONFIG_ERROR
    assert "at least one" in capsys.readouterr().err


def test_run_team_rejects_a_duplicate_role_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["run-team", "--role", "builder:do a", "--role", "builder:do b"])

    assert exit_code == cli.EXIT_CONFIG_ERROR
    assert "declared more than once" in capsys.readouterr().err
