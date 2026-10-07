"""Unit: the environment an agent or an install is given (ADR-0006, ADR-0029, V5-E4)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from cuttlefish.delegate.subprocess_env import (
    merge_env,
    project_overlay,
    scrub_own_venv,
    withheld_names,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from a small, known environment so what is and is not passed is exact."""
    for name in list(os.environ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/someone")


# --- the allowlist -----------------------------------------------------------------------


def test_nothing_is_passed_that_is_not_on_the_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_AMBIENT_VAR", "ambient-value")

    merged = merge_env(None)

    assert "SOME_AMBIENT_VAR" not in merged
    assert merged["HOME"] == "/home/someone"
    assert merged["PATH"] == "/usr/bin:/bin"


def test_cuttlefishs_own_secrets_never_reach_a_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """What `load_dotenv()` puts in the daemon's environment must not reach an agent's shell."""
    for name in (
        "CUTTLEFISH_SECRETS_KEY",
        "E2B_API_KEY",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "CUTTLEFISH_SERVE_PASSWORD",
    ):
        monkeypatch.setenv(name, "s3cret")

    merged = merge_env(None)

    assert not [name for name in merged if "KEY" in name or "PASSWORD" in name]
    assert "s3cret" not in merged.values()


def test_locale_terminal_proxy_and_toolchain_names_are_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wanted = {
        "LANG": "en_GB.UTF-8",
        "LC_ALL": "C",
        "TERM": "xterm",
        "XDG_CACHE_HOME": "/home/someone/.cache",
        "HTTPS_PROXY": "http://proxy:3128",
        "no_proxy": "localhost",
        "SSL_CERT_FILE": "/etc/ssl/ca.pem",
        "GOPATH": "/home/someone/go",
        "CARGO_HOME": "/home/someone/.cargo",
        "JAVA_HOME": "/usr/lib/jvm/x",
    }
    for name, value in wanted.items():
        monkeypatch.setenv(name, value)

    merged = merge_env(None)

    assert {name: merged[name] for name in wanted} == wanted


def test_a_declared_credential_is_added_and_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANG", "C")

    merged = merge_env({"OPENROUTER_API_KEY": "from-secret", "LANG": "declared"})

    assert merged["OPENROUTER_API_KEY"] == "from-secret"
    assert merged["LANG"] == "declared"


def test_an_empty_declaration_still_gives_a_usable_environment() -> None:
    for declared in (None, {}):
        merged = merge_env(declared)
        assert merged["PATH"] and merged["HOME"]


def test_the_operators_passthrough_adds_names_and_prefixes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUTTLEFISH_AGENT_ENV_PASSTHROUGH", " MY_TOKEN , AWS_* ,")
    monkeypatch.setenv("MY_TOKEN", "t")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.setenv("AWS_PROFILE", "dev")
    monkeypatch.setenv("OTHER", "x")

    merged = merge_env(None)

    assert (merged["MY_TOKEN"], merged["AWS_REGION"], merged["AWS_PROFILE"]) == (
        "t",
        "eu-west-1",
        "dev",
    )
    assert "OTHER" not in merged


def test_a_backends_own_names_are_passed_only_to_that_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_HOME", "/home/someone/.codex")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://x")

    assert "CODEX_HOME" not in merge_env(None)
    merged = merge_env(None, passthrough=("CODEX_*", "OPENAI_BASE_URL"))

    assert (
        merged["CODEX_HOME"] == "/home/someone/.codex" and merged["OPENAI_BASE_URL"] == "http://x"
    )


def test_package_tool_settings_reach_an_install_but_not_an_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NPM_TOKEN", "n")
    monkeypatch.setenv("npm_config_registry", "https://registry.example")
    monkeypatch.setenv("UV_INDEX_URL", "https://pypi.example")
    monkeypatch.setenv("PIP_INDEX_URL", "https://pypi.example")

    agent, install = merge_env(None), merge_env(None, tools=True)

    assert not [n for n in agent if n.startswith(("NPM_", "npm_config_", "UV_", "PIP_"))]
    assert {"NPM_TOKEN", "npm_config_registry", "UV_INDEX_URL", "PIP_INDEX_URL"} <= set(install)


def test_the_other_ecosystems_install_settings_reach_an_install_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = [
        "POETRY_HTTP_BASIC_PRIVATE_PASSWORD",
        "GOPROXY",
        "GOPRIVATE",
        "CARGO_REGISTRIES_X_TOKEN",
    ]
    names += ["BUNDLE_GEMS__EXAMPLE__COM", "GRADLE_USER_HOME", "MAVEN_OPTS", "PIPENV_PYPI_MIRROR"]
    for name in names:
        monkeypatch.setenv(name, "v")

    agent, install = merge_env(None), merge_env(None, tools=True)

    assert not set(names) & set(agent)
    assert set(names) <= set(install)


def test_withheld_names_lists_names_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_SECRET", "hunter2")
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setenv("CUTTLEFISH_AGENT_ENV_PASSTHROUGH", "ALSO_OK")
    monkeypatch.setenv("ALSO_OK", "1")

    names = withheld_names()

    assert "SOME_SECRET" in names and "LANG" not in names and "ALSO_OK" not in names
    assert "hunter2" not in " ".join(names)


# --- PATH --------------------------------------------------------------------------------


def test_cuttlefishs_own_venv_is_dropped_from_path_and_virtual_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    own = tmp_path / "cuttlefish" / ".venv"
    (own / "bin").mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(own))
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    monkeypatch.setenv("VIRTUAL_ENV", str(own))
    monkeypatch.setenv("PATH", os.pathsep.join([str(own / "bin"), "/usr/local/bin", "/usr/bin"]))

    merged = merge_env(None)

    assert "VIRTUAL_ENV" not in merged
    assert merged["PATH"].split(os.pathsep) == ["/usr/local/bin", "/usr/bin"]


def test_another_venvs_path_entry_is_left_alone_but_virtual_env_is_not_inherited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    own, project = tmp_path / "cuttlefish" / ".venv", tmp_path / "project" / ".venv"
    (own / "bin").mkdir(parents=True)
    (project / "bin").mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(own))
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    monkeypatch.setenv("VIRTUAL_ENV", str(project))
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(project / "bin"), str(own / "bin"), "/usr/bin"])
    )

    merged = merge_env(None)

    assert merged["PATH"].split(os.pathsep) == [str(project / "bin"), "/usr/bin"]
    assert "VIRTUAL_ENV" not in merged  # only the project's overlay (given a root) sets it


def test_scrub_own_venv_keeps_everything_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    own = tmp_path / ".venv"
    (own / "bin").mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(own))
    monkeypatch.setattr(sys, "base_prefix", "/usr")

    scrubbed = scrub_own_venv({"PATH": f"{own / 'bin'}:/usr/bin", "KEEP": "1"})

    assert scrubbed == {"PATH": "/usr/bin", "KEEP": "1"}


def test_wsl_windows_directories_are_dropped_unless_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/mnt/c/Python313:/mnt/c/Windows/system32:/bin")

    assert merge_env(None)["PATH"] == "/usr/bin:/bin"
    monkeypatch.setenv("CUTTLEFISH_KEEP_WINDOWS_PATH", "1")
    assert "/mnt/c/Python313" in merge_env(None)["PATH"]


def test_a_path_that_only_looks_like_mnt_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/mntx/bin:/opt/mnt/bin")

    assert merge_env(None)["PATH"] == "/usr/bin:/mntx/bin:/opt/mnt/bin"


# --- the project's own environment ---------------------------------------------------------


def _make_venv(root: Path) -> None:
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")


def test_a_projects_venv_is_first_on_path_with_virtual_env(tmp_path: Path) -> None:
    _make_venv(tmp_path)

    merged = merge_env(None, root=tmp_path)

    assert merged["VIRTUAL_ENV"] == str(tmp_path / ".venv")
    assert merged["PATH"].split(os.pathsep)[0] == str(tmp_path / ".venv" / "bin")
    assert merged["PATH"].endswith("/usr/bin:/bin")


def test_node_modules_bin_is_on_path_when_it_exists(tmp_path: Path) -> None:
    (tmp_path / "node_modules" / ".bin").mkdir(parents=True)

    merged = merge_env(None, root=tmp_path)

    assert merged["PATH"].split(os.pathsep)[0] == str(tmp_path / "node_modules" / ".bin")
    assert "VIRTUAL_ENV" not in merged


def test_both_overlays_come_ahead_of_the_system_path(tmp_path: Path) -> None:
    _make_venv(tmp_path)
    (tmp_path / "node_modules" / ".bin").mkdir(parents=True)

    entries = merge_env(None, root=tmp_path)["PATH"].split(os.pathsep)

    assert entries[:2] == [str(tmp_path / ".venv" / "bin"), str(tmp_path / "node_modules" / ".bin")]


def test_a_project_without_either_changes_nothing(tmp_path: Path) -> None:
    assert merge_env(None, root=tmp_path)["PATH"] == "/usr/bin:/bin"
    assert project_overlay(tmp_path, "/usr/bin") == {}


def test_a_dot_venv_that_is_not_a_venv_is_ignored(tmp_path: Path) -> None:
    (tmp_path / ".venv" / "bin").mkdir(parents=True)  # no pyvenv.cfg: a half-made or foreign dir

    assert "VIRTUAL_ENV" not in merge_env(None, root=tmp_path)


def test_a_declared_value_still_beats_the_overlay(tmp_path: Path) -> None:
    _make_venv(tmp_path)

    assert merge_env({"VIRTUAL_ENV": "/explicit"}, root=tmp_path)["VIRTUAL_ENV"] == "/explicit"


def test_a_declared_credential_is_not_reported_as_withheld(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-x")
    monkeypatch.setenv("E2B_API_KEY", "e2b_x")

    with caplog.at_level(logging.DEBUG, logger="cuttlefish.delegate.subprocess_env"):
        merged = merge_env({"OPENROUTER_API_KEY": "sk-or-x"})

    message = caplog.records[-1].getMessage()
    assert merged["OPENROUTER_API_KEY"] == "sk-or-x"
    assert "E2B_API_KEY" in message and "OPENROUTER_API_KEY" not in message
    assert "sk-or-x" not in message and "e2b_x" not in message
