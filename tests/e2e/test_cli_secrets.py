"""End-to-end: `cuttlefish secrets ...` and `cuttlefish run --project/--secret`,
in-process (ADR-0006) -- the same style tests/e2e/test_cli.py already uses.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from cuttlefish import cli, runtime
from cuttlefish.secrets.store import generate_key


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    yield
    runtime.reset()


def _set_secret(
    monkeypatch: pytest.MonkeyPatch, *args: str, value: str, capsys: pytest.CaptureFixture[str]
) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{value}\n"))
    exit_code = cli.main(["secrets", "set", *args])
    capsys.readouterr()
    return exit_code


def test_generate_key_prints_a_usable_fernet_key(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["secrets", "generate-key"])
    assert exit_code == cli.EXIT_OK
    from cryptography.fernet import Fernet

    Fernet(capsys.readouterr().out.strip().encode("ascii"))


def test_set_then_get_round_trips_a_project_scoped_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CUTTLEFISH_SECRETS_KEY", generate_key())

    assert (
        _set_secret(
            monkeypatch, "--project", "demo", "HUGGINGFACE_TOKEN", value="hf_x", capsys=capsys
        )
        == cli.EXIT_OK
    )

    exit_code = cli.main(["secrets", "get", "--project", "demo", "HUGGINGFACE_TOKEN"])
    assert exit_code == cli.EXIT_OK
    assert capsys.readouterr().out.strip() == "hf_x"


def test_shared_scope_secrets_are_readable_by_name_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CUTTLEFISH_SECRETS_KEY", generate_key())

    _set_secret(monkeypatch, "--shared", "OPENROUTER_API_KEY", value="shared-key", capsys=capsys)

    exit_code = cli.main(["secrets", "get", "--shared", "OPENROUTER_API_KEY"])
    assert exit_code == cli.EXIT_OK
    assert capsys.readouterr().out.strip() == "shared-key"


def test_list_prints_names_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CUTTLEFISH_SECRETS_KEY", generate_key())

    _set_secret(monkeypatch, "--project", "demo", "TOKEN_B", value="secret-value", capsys=capsys)
    _set_secret(monkeypatch, "--project", "demo", "TOKEN_A", value="another-value", capsys=capsys)

    cli.main(["secrets", "list", "--project", "demo"])
    out = capsys.readouterr().out
    assert out.splitlines() == ["TOKEN_A", "TOKEN_B"]
    assert "secret-value" not in out
    assert "another-value" not in out


def test_delete_removes_a_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CUTTLEFISH_SECRETS_KEY", generate_key())

    _set_secret(monkeypatch, "--project", "demo", "TOKEN", value="value", capsys=capsys)
    assert cli.main(["secrets", "delete", "--project", "demo", "TOKEN"]) == cli.EXIT_OK
    capsys.readouterr()

    exit_code = cli.main(["secrets", "get", "--project", "demo", "TOKEN"])
    assert exit_code == cli.EXIT_TASK_FAILED
    assert "no secret" in capsys.readouterr().err


def test_managing_secrets_with_no_key_set_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CUTTLEFISH_SECRETS_KEY", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["secrets", "get", "--project", "demo", "TOKEN"])

    assert exc_info.value.code == cli.EXIT_CONFIG_ERROR
    assert "CUTTLEFISH_SECRETS_KEY" in capsys.readouterr().err


def test_run_with_a_declared_secret_but_no_key_configured_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A binary that's always on PATH (this check runs before the secrets one)
    # and the keyless LLM provider -- this test is about the secrets config
    # error, not whether kopicode/a live model credential are actually
    # available (CI's unit-test job deliberately has neither).
    monkeypatch.setenv("CUTTLEFISH_KOPICODE_BIN", "true")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CUTTLEFISH_SECRETS_KEY", raising=False)

    exit_code = cli.main(["run", "add a .gitignore entry", "--secret", "HUGGINGFACE_TOKEN"])

    assert exit_code == cli.EXIT_CONFIG_ERROR
    assert "CUTTLEFISH_SECRETS_KEY" in capsys.readouterr().err


def test_run_with_a_declared_secret_not_in_either_scope_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CUTTLEFISH_KOPICODE_BIN", "true")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CUTTLEFISH_SECRETS_KEY", generate_key())

    exit_code = cli.main(["run", "add a .gitignore entry", "--secret", "NEVER_SET_ANYWHERE"])

    assert exit_code == cli.EXIT_CONFIG_ERROR
    assert "NEVER_SET_ANYWHERE" in capsys.readouterr().err
