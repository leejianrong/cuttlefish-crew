"""Unit: `cuttlefish doctor`'s checks, each a pure function of what it is handed."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cuttlefish import doctor
from cuttlefish.projects.store import Project
from cuttlefish.secrets.store import generate_key


def _by_name(checks: list[doctor.Check], name: str) -> doctor.Check:
    return next(c for c in checks if c.name == name)


def test_a_backend_on_path_reports_its_location_and_version() -> None:
    checks = doctor.check_backends(
        {}, which=lambda b: f"/bin/{b}" if b == "kopicode" else None, version=lambda _: "v0.3.0"
    )

    assert _by_name(checks, "backend kopicode").status == "ok"
    assert "/bin/kopicode (v0.3.0)" in _by_name(checks, "backend kopicode").detail
    missing = _by_name(checks, "backend codex")
    assert missing.status == "warn" and "CUTTLEFISH_CODEX_BIN" in missing.detail


def test_the_env_example_placeholder_secrets_key_fails() -> None:
    checks = doctor.check_credentials(
        {"CUTTLEFISH_SECRETS_KEY": "REPLACE_WITH_A_GENERATED_FERNET_KEY"}
    )

    check = _by_name(checks, "CUTTLEFISH_SECRETS_KEY")
    assert check.status == "fail"
    assert "generate-key" in check.detail


def test_a_garbage_secrets_key_fails_and_a_real_one_passes() -> None:
    bad = doctor.check_credentials({"CUTTLEFISH_SECRETS_KEY": "not-a-fernet-key"})
    good = doctor.check_credentials({"CUTTLEFISH_SECRETS_KEY": generate_key()})

    assert _by_name(bad, "CUTTLEFISH_SECRETS_KEY").status == "fail"
    assert _by_name(good, "CUTTLEFISH_SECRETS_KEY").detail == "set and valid"


def test_an_unset_secrets_key_is_fine() -> None:
    assert _by_name(doctor.check_credentials({}), "CUTTLEFISH_SECRETS_KEY").status == "ok"


def test_credential_values_never_appear_in_any_check() -> None:
    environ = {
        "OPENROUTER_API_KEY": "sk-or-a-real-looking-secret",
        "CUTTLEFISH_SECRETS_KEY": generate_key(),
    }

    rendered = "\n".join(c.render() for c in doctor.check_credentials(environ))

    assert "sk-or-a-real-looking-secret" not in rendered
    assert environ["CUTTLEFISH_SECRETS_KEY"] not in rendered
    assert "OPENROUTER_API_KEY: set" in rendered


def test_a_placeholder_provider_key_fails() -> None:
    checks = doctor.check_credentials({"OPENROUTER_API_KEY": "REPLACE_WITH_YOUR_OPENROUTER_KEY"})

    assert _by_name(checks, "OPENROUTER_API_KEY").status == "fail"


def test_path_warns_about_cuttlefishs_own_venv_and_windows_entries(tmp_path: Path) -> None:
    own = tmp_path / ".venv"
    (own / "bin").mkdir(parents=True)
    path = os.pathsep.join([str(own / "bin"), "/usr/bin", "/mnt/c/Python313"])

    checks = doctor.check_path({"PATH": path}, prefix=str(own))

    details = " ".join(c.detail for c in checks)
    assert all(c.status == "warn" for c in checks)
    assert "own venv" in details and "Windows entries" in details


def test_a_clean_path_is_ok(tmp_path: Path) -> None:
    checks = doctor.check_path({"PATH": "/usr/bin:/bin"}, prefix=str(tmp_path / ".venv"))

    assert [c.status for c in checks] == ["ok"]


def test_the_log_check_reports_an_existing_file_and_a_creatable_one(tmp_path: Path) -> None:
    existing = tmp_path / "cuttlefish.log"
    existing.write_text("hello")

    assert doctor.check_log(existing)[0].detail.endswith("(5 bytes)")
    assert doctor.check_log(tmp_path / "logs" / "cuttlefish.log")[0].status == "ok"


def _project(root: Path, name: str = "flowers") -> Project:
    return Project(id="id1", name=name, root=str(root), secrets_scope=name)


def test_a_missing_project_root_fails(tmp_path: Path) -> None:
    checks = doctor.check_projects([_project(tmp_path / "gone")])

    assert checks[0].status == "fail"


def test_an_empty_secrets_db_in_a_project_is_flagged(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".cuttlefish").mkdir()
    (tmp_path / ".cuttlefish" / "secrets.db").write_bytes(b"")

    check = doctor.check_projects([_project(tmp_path)])[0]

    assert check.status == "warn"
    assert "secrets.db is empty" in check.detail


def test_a_healthy_project_is_ok_and_says_what_its_files_need(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "uv.lock").write_text("")

    check = doctor.check_projects([_project(tmp_path)])[0]

    assert check.status == "ok"
    assert "Python (uv, .venv missing)" in check.detail


def test_exit_code_is_nonzero_only_for_a_failure() -> None:
    assert doctor.exit_code([doctor.Check("warn", "x", "y")]) == 0
    assert doctor.exit_code([doctor.Check("fail", "x", "y")]) == 1


def test_doctor_runs_end_to_end_from_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from cuttlefish import cli

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CUTTLEFISH_SECRETS_KEY", "REPLACE_WITH_A_GENERATED_FERNET_KEY")

    code = cli.main(["doctor"])

    out = capsys.readouterr().out
    assert code != 0
    assert "[FAIL] CUTTLEFISH_SECRETS_KEY" in out
    assert "REPLACE_WITH_A_GENERATED_FERNET_KEY" not in out.replace("placeholder", "")
