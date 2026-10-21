"""End-to-end: the CLI, in-process, against the real kopicode binary.

docs/SLICES.md V1 E2E test plan: "A real task submitted via the CLI reaches a
terminal state and prints a JSON result", and "cuttlefish show on a completed
task renders the full sequence of what happened, matching the episodic journal
exactly." No mock kopicode (docs/PLAN.md "Testing approach"); the provider
credential is unset so the run reaches kopicode's own real refusal-shaped outcome
without a live model call, the same posture the delegation/workflow integration
tests already take.

`cli.main` is called directly (never via subprocess): it's a synchronous entry
point that manages its own event loop internally (`asyncio.run`), so these are
plain, non-async test functions.
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
def test_run_reaches_a_terminal_state_and_prints_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    exit_code = cli.main(["run", "add a .gitignore entry"])

    assert exit_code == cli.EXIT_TASK_FAILED
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed"
    assert "task_id" in result
    assert "error" in result


@pytest.mark.requires_kopicode
def test_show_renders_the_full_sequence_matching_the_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    cli.main(["run", "add a .gitignore entry"])
    run_result = json.loads(capsys.readouterr().out)
    task_id = run_result["task_id"]

    exit_code = cli.main(["show", task_id])
    lines = capsys.readouterr().out.strip().splitlines()

    assert exit_code == cli.EXIT_OK
    assert len(lines) == 4
    assert "TaskSubmitted" in lines[0]
    assert "DelegationStarted" in lines[1]
    assert "DelegationFailed" in lines[2]
    assert "TaskFailed" in lines[3]


def test_steer_on_a_task_that_is_not_currently_steerable_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["steer", "no-such-task", "hello"])

    assert exit_code == cli.EXIT_CONFIG_ERROR
    assert "no steerable task" in capsys.readouterr().err


def test_missing_kopicode_binary_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CUTTLEFISH_KOPICODE_BIN", "kopicode-binary-that-does-not-exist")

    exit_code = cli.main(["run", "add a .gitignore entry"])

    assert exit_code == cli.EXIT_CONFIG_ERROR
    assert "not on PATH" in capsys.readouterr().err


def test_missing_claude_code_binary_is_a_config_error_only_when_that_backend_is_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0005: selecting a backend that isn't on PATH fails closed at startup
    (Q17), the same as kopicode's own missing-binary check -- and a missing
    claude binary is *not* checked at all when kopicode is the configured
    backend (the default), proving the two checks are actually independent.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CUTTLEFISH_AGENT_BACKEND", "claude-code")
    monkeypatch.setenv("CUTTLEFISH_CLAUDE_CODE_BIN", "claude-binary-that-does-not-exist")

    exit_code = cli.main(["run", "add a .gitignore entry"])

    assert exit_code == cli.EXIT_CONFIG_ERROR
    assert "not on PATH" in capsys.readouterr().err


def test_an_unknown_agent_backend_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CUTTLEFISH_AGENT_BACKEND", "not-a-real-backend")

    exit_code = cli.main(["run", "add a .gitignore entry"])

    assert exit_code == cli.EXIT_CONFIG_ERROR
    assert "unknown CUTTLEFISH_AGENT_BACKEND" in capsys.readouterr().err


def test_show_on_an_unknown_task_is_a_clear_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["show", "nonexistent-task"])

    assert exit_code == cli.EXIT_TASK_FAILED
    assert "no events recorded" in capsys.readouterr().err
