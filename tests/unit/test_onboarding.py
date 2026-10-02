from __future__ import annotations

from pathlib import Path

from cuttlefish import cli, onboarding
from cuttlefish.projects.store import ProjectStore


def _which(*present: str):  # type: ignore[no-untyped-def]
    return lambda name: f"/bin/{name}" if name in present else None


def test_detect_prefers_configured_backend() -> None:
    assert onboarding.detect_backend({"CUTTLEFISH_AGENT_BACKEND": "codex"}, _which()) == "codex"


def test_detect_falls_back_to_first_cli_on_path() -> None:
    assert onboarding.detect_backend({}, _which("codex", "claude")) == "claude-code"
    assert onboarding.detect_backend({}, _which()) is None


def test_checks_flag_missing_cli_and_login(tmp_path: Path) -> None:
    checks = onboarding.check_prerequisites("codex", env={}, which=_which("uv"), home=tmp_path)
    by_name = {c.name: c for c in checks}
    assert by_name["uv"].ok
    assert not by_name["codex CLI"].ok
    assert not by_name["codex login"].ok
    assert by_name["summarising key (optional)"].ok  # never blocks (KAN-1807)


def test_checks_pass_when_everything_present(tmp_path: Path) -> None:
    (tmp_path / ".codex").mkdir()
    checks = onboarding.check_prerequisites(
        "codex", env={}, which=_which("uv", "codex"), home=tmp_path
    )
    assert all(c.ok for c in checks)


def test_register_or_reuse_is_idempotent(tmp_path: Path) -> None:
    store = ProjectStore.open(tmp_path / "p.db")
    root = tmp_path / "repo"
    root.mkdir()
    first, created = onboarding.register_or_reuse(
        store, name="repo", root=root, roles=onboarding.DEFAULT_ROLES
    )
    second, created_again = onboarding.register_or_reuse(store, name="other", root=root, roles=())
    assert created and not created_again
    assert second.id == first.id
    assert [r.name for r in first.roles] == ["builder", "reviewer"]
    assert len(store.list()) == 1
    store.close()


def test_init_command_end_to_end(
    tmp_path: Path,
    monkeypatch,
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    (tmp_path / "home" / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CUTTLEFISH_AGENT_BACKEND", "codex")
    monkeypatch.setattr("shutil.which", _which("uv", "codex"))
    repo = tmp_path / "repo"
    repo.mkdir()
    assert cli.main(["init", "--root", str(repo)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "registered" in out and "CUTTLEFISH_AGENT_BACKEND=codex uv run cuttlefish run" in out
    assert cli.main(["init", "--root", str(repo)]) == cli.EXIT_OK
    assert "already registered" in capsys.readouterr().out


def test_init_fails_with_no_agent_cli(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("CUTTLEFISH_AGENT_BACKEND", raising=False)
    monkeypatch.setattr("shutil.which", _which("uv"))
    assert cli.main(["init"]) == cli.EXIT_CONFIG_ERROR
    assert "no coding-agent CLI" in capsys.readouterr().err
