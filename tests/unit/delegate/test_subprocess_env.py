"""Unit: merge_env keeps the ambient environment, never cuttlefish's own venv (ADR-0029)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from cuttlefish.delegate.subprocess_env import merge_env


def test_no_declared_env_still_yields_the_ambient_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never `None` (full inheritance would leak cuttlefish's venv) and never empty
    (an empty `env=` leaves the child no PATH at all)."""
    monkeypatch.setenv("SOME_AMBIENT_VAR", "ambient-value")

    for declared in (None, {}):
        merged = merge_env(declared)
        assert merged["SOME_AMBIENT_VAR"] == "ambient-value"
        assert merged["PATH"]


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


def test_another_virtual_env_is_left_alone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    own = tmp_path / "cuttlefish" / ".venv"
    project = tmp_path / "project" / ".venv"
    (own / "bin").mkdir(parents=True)
    (project / "bin").mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(own))
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    monkeypatch.setenv("VIRTUAL_ENV", str(project))
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(project / "bin"), str(own / "bin"), "/usr/bin"])
    )

    merged = merge_env(None)

    assert merged["VIRTUAL_ENV"] == str(project)
    assert merged["PATH"].split(os.pathsep) == [str(project / "bin"), "/usr/bin"]


def test_a_dotenv_value_in_the_daemon_environment_is_still_inherited_until_the_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins today's behaviour so V5-E4's allowlist (Q58) changes it on purpose: a value
    `load_dotenv()` put in `os.environ` reaches the agent's environment."""
    monkeypatch.setenv("CUTTLEFISH_SECRETS_KEY", "from-dotenv")

    assert merge_env(None)["CUTTLEFISH_SECRETS_KEY"] == "from-dotenv"


def test_a_nonempty_mapping_merges_over_a_copy_of_os_environ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOME_AMBIENT_VAR", "ambient-value")

    merged = merge_env({"HUGGINGFACE_TOKEN": "hf_value"})

    assert merged["SOME_AMBIENT_VAR"] == "ambient-value"
    assert merged["HUGGINGFACE_TOKEN"] == "hf_value"


def test_a_declared_value_overrides_the_same_name_already_in_os_environ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")

    merged = merge_env({"ANTHROPIC_API_KEY": "from-secret"})

    assert merged["ANTHROPIC_API_KEY"] == "from-secret"
