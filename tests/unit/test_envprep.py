"""Unit: planning and running dependency installs (ADR-0029, V5-E3).

The tools are shell-script shims on a private ``PATH``; nothing here installs anything.
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from cuttlefish import envprep
from cuttlefish.environment import detect


def _write(root: Path, name: str, text: str = "") -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _plan(root: Path) -> envprep.PreparePlan:
    return envprep.plan(root)


def _uv_project(root: Path, *, venv: bool = False) -> None:
    _write(root, "pyproject.toml", '[project]\nname = "x"\n')
    _write(root, "uv.lock", "lock-1")
    if venv:
        _write(root, ".venv/pyvenv.cfg")


# --- planning ---------------------------------------------------------------------------


def test_a_uv_project_without_a_venv_needs_a_frozen_sync(tmp_path: Path) -> None:
    _uv_project(tmp_path)

    found = _plan(tmp_path)

    (step,) = found.steps
    assert (step.ecosystem, step.commands) == ("python", (("uv", "sync", "--frozen"),))
    assert step.reason == ".venv is missing"


def test_without_a_lockfile_uv_sync_is_not_frozen(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", '[tool.uv]\n[project]\nname = "x"\n')

    (step,) = _plan(tmp_path).steps

    assert step.commands == (("uv", "sync"),)


def test_a_requirements_project_gets_a_venv_then_the_requirements(tmp_path: Path) -> None:
    _write(tmp_path, "requirements.txt", "numpy\n")
    _write(tmp_path, "requirements-dev.txt", "pytest\n")

    (step,) = _plan(tmp_path).steps

    assert step.commands == (
        ("uv", "venv", "--allow-existing"),
        ("uv", "pip", "install", "-r", "requirements.txt", "-r", "requirements-dev.txt"),
    )


@pytest.mark.parametrize(
    ("files", "command"),
    [
        (("package-lock.json",), ("npm", "ci")),
        ((), ("npm", "install")),
        (("pnpm-lock.yaml",), ("pnpm", "install", "--frozen-lockfile")),
        (("yarn.lock",), ("yarn", "install", "--frozen-lockfile")),
        (("yarn.lock", ".yarnrc.yml"), ("yarn", "install", "--immutable")),
        (("bun.lockb",), ("bun", "install", "--frozen-lockfile")),
    ],
)
def test_node_commands_follow_the_package_tool(
    tmp_path: Path, files: tuple[str, ...], command: tuple[str, ...]
) -> None:
    _write(tmp_path, "package.json", "{}")
    for name in files:
        _write(tmp_path, name)

    (step,) = _plan(tmp_path).steps

    assert (step.ecosystem, step.commands, step.reason) == (
        "node",
        (command,),
        "node_modules is missing",
    )


def test_an_install_that_is_there_and_matches_needs_nothing(tmp_path: Path) -> None:
    _uv_project(tmp_path, venv=True)
    found = _plan(tmp_path)
    envprep.record_adopted(tmp_path, found)

    assert _plan(tmp_path).steps == ()
    assert [e for e, _ in found.adopt] == ["python"]
    assert envprep.read_state(tmp_path)["python"]["how"] == "adopted"


def test_a_persons_own_install_is_trusted_until_its_files_change(tmp_path: Path) -> None:
    _uv_project(tmp_path, venv=True)
    envprep.record_adopted(tmp_path, _plan(tmp_path))

    _write(tmp_path, "uv.lock", "lock-2")
    (step,) = _plan(tmp_path).steps

    assert step.reason == "uv.lock changed since the last install"


def test_a_version_hint_change_makes_it_stale_too(tmp_path: Path) -> None:
    _uv_project(tmp_path, venv=True)
    envprep.record_adopted(tmp_path, _plan(tmp_path))

    _write(tmp_path, ".python-version", "3.13\n")

    assert len(_plan(tmp_path).steps) == 1


def test_without_a_record_a_present_install_is_not_stale(tmp_path: Path) -> None:
    _uv_project(tmp_path, venv=True)

    found = _plan(tmp_path)

    assert found.steps == ()
    assert len(found.adopt) == 1


def test_ecosystems_cuttlefish_cannot_install_are_listed_not_hidden(tmp_path: Path) -> None:
    _write(tmp_path, "go.mod", "module x\n\ngo 1.22\n")
    _write(tmp_path, "pyproject.toml", "[tool.poetry]\nname = 'x'\n")
    _write(tmp_path, ".venv/pyvenv.cfg")

    found = _plan(tmp_path)

    assert found.steps == ()
    reasons = dict(found.unsupported)
    assert "does not install this one yet" in reasons["go"]
    assert reasons["python"] == "poetry projects are not prepared yet"


def test_a_pip_project_with_only_pyproject_says_why_it_is_not_prepared(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", '[project]\nname = "x"\n')

    found = _plan(tmp_path)

    assert found.steps == ()
    assert dict(found.unsupported)["python"] == "no requirements.txt to install from"


def test_plan_runs_nothing_and_to_json_is_serialisable(tmp_path: Path) -> None:
    import json

    _uv_project(tmp_path)

    body = _plan(tmp_path).to_json()

    assert body["steps"][0]["name"] == "Python"
    json.dumps(body)


def test_the_fingerprint_moves_with_the_files_that_decide_an_install(tmp_path: Path) -> None:
    _uv_project(tmp_path)
    env = detect(tmp_path).ecosystems[0]
    before = envprep.fingerprint(tmp_path, env)

    assert envprep.fingerprint(tmp_path, env) == before
    _write(tmp_path, "uv.lock", "something else")
    assert envprep.fingerprint(tmp_path, env) != before


def test_a_corrupt_state_file_reads_as_empty_and_is_replaced(tmp_path: Path) -> None:
    _write(tmp_path, ".cuttlefish/env.json", "{ nope")

    assert envprep.read_state(tmp_path) == {}
    envprep.write_state(tmp_path, "node", fingerprint="abc", how="prepared")
    assert envprep.read_state(tmp_path)["node"]["fingerprint"] == "abc"


def test_an_unwritable_state_location_does_not_raise(tmp_path: Path) -> None:
    (tmp_path / ".cuttlefish").write_text("a file where the folder should be")

    envprep.write_state(tmp_path, "node", fingerprint="abc", how="prepared")  # must not raise


# --- running ----------------------------------------------------------------------------


@pytest.fixture
def shims(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin")
    return bin_dir


def _shim(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _step(*commands: tuple[str, ...]) -> envprep.PrepareStep:
    return envprep.PrepareStep(
        ecosystem="python", commands=commands, reason="test", fingerprint="f"
    )


async def test_a_successful_step_runs_in_the_project_root_and_reports_its_output(
    tmp_path: Path, shims: Path
) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    _shim(shims, "uv", 'echo "installing in $(pwd)"; mkdir -p .venv; touch .venv/pyvenv.cfg')

    result = await envprep.run_step(_step(("uv", "sync")), root)

    assert result.ok and result.exit_code == 0
    assert f"installing in {root}" in result.tail
    assert (root / ".venv" / "pyvenv.cfg").exists()


async def test_commands_run_in_order_and_stop_at_the_first_failure(
    tmp_path: Path, shims: Path
) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    _shim(shims, "uv", 'echo "$1" >> ran.txt; [ "$1" = "venv" ] && exit 3; exit 0')

    result = await envprep.run_step(_step(("uv", "venv"), ("uv", "pip")), root)

    assert (result.ok, result.exit_code, result.failure) == (False, 3, "exit")
    assert (root / "ran.txt").read_text().split() == ["venv"]
    assert result.commands_run == 1


async def test_a_failure_keeps_the_tail_of_its_output(tmp_path: Path, shims: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    _shim(shims, "npm", 'for i in $(seq 1 200); do echo "line $i"; done; echo "boom" >&2; exit 1')

    result = await envprep.run_step(_step(("npm", "ci")), root)

    assert result.failure == "exit"
    assert "boom" in result.tail and "line 200" in result.tail
    assert "line 1\n" not in result.tail  # only the end is kept
    assert len(result.tail) <= 4000


async def test_a_tool_that_is_not_on_path_is_a_clear_failure(tmp_path: Path, shims: Path) -> None:
    result = await envprep.run_step(_step(("pnpm", "install")), tmp_path)

    assert (result.ok, result.failure, result.exit_code) == (False, "tool_missing", None)
    assert "'pnpm' is not on PATH" in result.tail


async def test_a_step_that_runs_too_long_is_killed(tmp_path: Path, shims: Path) -> None:
    _shim(shims, "npm", "sleep 30")

    started = time.monotonic()
    result = await envprep.run_step(_step(("npm", "ci")), tmp_path, timeout=0.4)

    assert (result.ok, result.failure) == (False, "timeout")
    assert time.monotonic() - started < 10


async def test_a_cancel_stops_the_step_and_its_children(tmp_path: Path, shims: Path) -> None:
    marker = tmp_path / "alive"
    _shim(shims, "npm", f"(sleep 30; touch {marker}) & wait")
    cancel = asyncio.Event()

    task = asyncio.ensure_future(envprep.run_step(_step(("npm", "ci")), tmp_path, cancel=cancel))
    await asyncio.sleep(0.3)
    cancel.set()
    result = await asyncio.wait_for(task, 10)

    assert (result.ok, result.cancelled, result.failure) == (False, True, "cancelled")


async def test_only_the_known_package_tools_may_be_run(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a program cuttlefish prepares with"):
        await envprep.run_step(_step(("rm", "-rf", "/")), tmp_path)


async def test_the_child_does_not_inherit_cuttlefishs_own_venv(
    tmp_path: Path, shims: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    own = tmp_path / "cuttlefish" / ".venv"
    (own / "bin").mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(own))
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    monkeypatch.setenv("VIRTUAL_ENV", str(own))
    monkeypatch.setenv("PATH", f"{shims}{os.pathsep}{own / 'bin'}{os.pathsep}/usr/bin:/bin")
    root = tmp_path / "proj"
    root.mkdir()
    _shim(shims, "uv", 'echo "$VIRTUAL_ENV|$PATH" > seen.txt')

    result = await envprep.run_step(_step(("uv", "sync")), root)

    assert result.ok
    virtual_env, path = (root / "seen.txt").read_text().strip().split("|", 1)
    assert virtual_env == "" and str(own) not in path


def test_the_timeout_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUTTLEFISH_PREPARE_TIMEOUT", "42")
    assert envprep.prepare_timeout() == 42.0
    monkeypatch.setenv("CUTTLEFISH_PREPARE_TIMEOUT", "nonsense")
    assert envprep.prepare_timeout() == envprep.DEFAULT_PREPARE_TIMEOUT_S
    monkeypatch.setenv("CUTTLEFISH_PREPARE_TIMEOUT", "-1")
    assert envprep.prepare_timeout() == envprep.DEFAULT_PREPARE_TIMEOUT_S


# --- failed installs and dependency-free projects (found by the real-tool run) ----------------


def test_a_failed_install_makes_a_half_made_venv_stale_again(tmp_path: Path) -> None:
    _uv_project(tmp_path, venv=True)  # `uv venv` made .venv, then the install failed
    current = envprep.fingerprint(tmp_path, detect(tmp_path).ecosystems[0])
    envprep.write_state(tmp_path, "python", fingerprint=current, how="failed")

    (step,) = _plan(tmp_path).steps

    assert step.reason == "the last install did not finish"


def test_a_failure_recorded_for_older_files_does_not_block_a_changed_project(
    tmp_path: Path,
) -> None:
    _uv_project(tmp_path, venv=True)
    envprep.write_state(tmp_path, "python", fingerprint="older", how="failed")

    (step,) = _plan(tmp_path).steps

    assert step.reason == "uv.lock changed since the last install"


def test_a_successful_install_that_made_no_folder_is_not_stale(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", "{}")
    _write(tmp_path, "package-lock.json", "{}")
    current = envprep.fingerprint(tmp_path, detect(tmp_path).ecosystems[0])
    envprep.write_state(tmp_path, "node", fingerprint=current, how="prepared", produced=False)

    assert _plan(tmp_path).steps == ()


def test_a_folder_the_install_made_and_a_person_deleted_is_stale(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", "{}")
    _write(tmp_path, "package-lock.json", "{}")
    current = envprep.fingerprint(tmp_path, detect(tmp_path).ecosystems[0])
    envprep.write_state(tmp_path, "node", fingerprint=current, how="prepared", produced=True)

    (step,) = _plan(tmp_path).steps

    assert step.reason == "node_modules is missing"


def test_produced_env_looks_for_the_ecosystems_own_folder(tmp_path: Path) -> None:
    assert not envprep.produced_env(tmp_path, "python")
    (tmp_path / ".venv").mkdir()
    (tmp_path / "node_modules").mkdir()
    assert envprep.produced_env(tmp_path, "python") and envprep.produced_env(tmp_path, "node")


async def test_the_install_environment_is_quiet_and_non_interactive(
    tmp_path: Path, shims: Path
) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    _shim(
        shims,
        "npm",
        'echo "$NO_UPDATE_NOTIFIER|$COREPACK_ENABLE_DOWNLOAD_PROMPT|$NO_COLOR" > seen.txt',
    )

    result = await envprep.run_step(_step(("npm", "ci")), root)

    assert result.ok
    assert (root / "seen.txt").read_text().strip() == "1|0|1"
