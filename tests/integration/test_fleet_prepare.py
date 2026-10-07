"""Integration: a daemon-started team installs its dependencies first (ADR-0029, V5-E3).

A real team under `FleetDaemon` (real satay, real `prepare_run`) with the scripted fake
`kopicode serve`, and `uv` as a shell-script shim on a private PATH that makes a fake `.venv`.
The install is journaled to the project's own episodic store ahead of the first round.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
import satay.control
from starlette.testclient import TestClient

from cuttlefish import envprep, runtime
from cuttlefish.episodic.events import (
    DelegationStarted,
    EnvironmentPrepared,
    EnvironmentPrepareStarted,
    TaskFailed,
    TeamStopped,
)
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.fleet.daemon import EnvironmentConfirmationError, FleetDaemon
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.projects.store import ProjectStore

FAKE = Path(__file__).parents[1] / "unit" / "delegate" / "fake_kopicode_serve.py"


@pytest.fixture(autouse=True)
def _reset_runtime() -> Any:
    yield
    runtime.reset()


def _fake_kopicode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = {
        "jsonrpc": "2.0",
        "id": "$start_id",
        "result": {"session": "$session", "record": "/r", "stop": "completed", "exit_code": 0},
    }
    ended = {
        "jsonrpc": "2.0",
        "method": "session.event",
        "params": {
            "session": "$session",
            "event": {"kind": "session_ended", "reason": "completed", "exit_code": 0},
        },
    }
    # Three sessions: the fake serves one scripted session per step list, and a daemon
    # reuses the one `kopicode serve` child across the starts in a test.
    session = [{"start": True}, {"emit": result}, {"close": [ended]}]
    scenario = [*session, *session, *session, {"eof": []}]
    (tmp_path / "scenario.json").write_text(json.dumps(scenario))
    monkeypatch.setenv("FAKE_KOPICODE_SCENARIO", str(tmp_path / "scenario.json"))
    monkeypatch.setenv("FAKE_KOPICODE_LOG", str(tmp_path / "sent.jsonl"))
    binary = tmp_path / "kopicode"
    binary.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n')
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("CUTTLEFISH_KOPICODE_BIN", str(binary))
    monkeypatch.setenv("CUTTLEFISH_LLM_PROVIDER", "replay")
    monkeypatch.delenv("CUTTLEFISH_SECRETS_KEY", raising=False)


def _uv_shim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> Path:
    """A `uv` that logs each call to `uv-calls.txt` and then runs `body`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    calls = tmp_path / "uv-calls.txt"
    shim = bin_dir / "uv"
    shim.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n{body}\n')
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return calls


_MAKE_VENV = "mkdir -p .venv/bin; touch .venv/pyvenv.cfg; echo installed"


def _project(tmp_path: Path, daemon: FleetDaemon, *, setting: str | None = None) -> Any:
    root = tmp_path / "alpha"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "x"\n')
    (root / "uv.lock").write_text("lock")
    project = daemon.projects.register(name="alpha", root=str(root))
    if setting is not None:
        project = daemon.projects.update_env_prepare(project.id, setting)
    return project


def _journal(root: str, team_id: str) -> list[Any]:
    store = EpisodicStore.open(Path(root) / ".cuttlefish" / "episodic.db")
    try:
        return [e.payload for e in store.read(team_id)]
    finally:
        store.close()


async def _until(condition: Any, seconds: float = 30) -> None:
    for _ in range(int(seconds / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timed out")


def _daemon(tmp_path: Path) -> FleetDaemon:
    return FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))


async def test_a_project_set_to_ask_refuses_to_start_until_told_what_to_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kopicode(tmp_path, monkeypatch)
    calls = _uv_shim(tmp_path, monkeypatch, _MAKE_VENV)
    daemon = _daemon(tmp_path)
    project = _project(tmp_path, daemon)

    with pytest.raises(EnvironmentConfirmationError, match="prepare=yes") as caught:
        await daemon.start(project.id, [{"name": "builder", "text": "go"}])

    assert [s.ecosystem for s in caught.value.plan.steps] == ["python"]
    assert daemon.running(project.id) is None
    assert not calls.exists()  # nothing was installed


async def test_prepare_yes_installs_before_the_first_round_and_journals_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kopicode(tmp_path, monkeypatch)
    calls = _uv_shim(tmp_path, monkeypatch, _MAKE_VENV)
    daemon = _daemon(tmp_path)
    project = _project(tmp_path, daemon)

    team_id = await daemon.start(project.id, [{"name": "builder", "text": "go"}], prepare="yes")
    await _until(lambda: not daemon.is_running(project.id))

    events = _journal(project.root, team_id)
    kinds = [type(e).__name__ for e in events]
    assert kinds.index("EnvironmentPrepareStarted") < kinds.index("EnvironmentPrepared")
    assert kinds.index("EnvironmentPrepared") < kinds.index("DelegationStarted")
    started = next(e for e in events if isinstance(e, EnvironmentPrepareStarted))
    done = next(e for e in events if isinstance(e, EnvironmentPrepared))
    assert (started.ecosystem, started.commands) == ("python", [["uv", "sync", "--frozen"]])
    assert started.reason == ".venv is missing"
    assert (done.ok, done.exit_code) == (True, 0) and "installed" in done.tail
    assert calls.read_text().strip() == "sync --frozen"
    assert (Path(project.root) / ".venv" / "pyvenv.cfg").exists()
    assert envprep.read_state(project.root)["python"]["how"] == "prepared"


async def test_a_second_start_with_nothing_changed_installs_nothing_and_does_not_ask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kopicode(tmp_path, monkeypatch)
    calls = _uv_shim(tmp_path, monkeypatch, _MAKE_VENV)
    daemon = _daemon(tmp_path)
    project = _project(tmp_path, daemon)
    await daemon.start(project.id, [{"name": "builder", "text": "go"}], prepare="yes")
    await _until(lambda: not daemon.is_running(project.id))

    team_id = await daemon.start(project.id, [{"name": "builder", "text": "again"}])
    await _until(lambda: not daemon.is_running(project.id))

    assert len(calls.read_text().splitlines()) == 1
    assert not any(
        isinstance(e, EnvironmentPrepareStarted) for e in _journal(project.root, team_id)
    )


async def test_a_changed_lockfile_is_installed_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kopicode(tmp_path, monkeypatch)
    calls = _uv_shim(tmp_path, monkeypatch, _MAKE_VENV)
    daemon = _daemon(tmp_path)
    project = _project(tmp_path, daemon, setting="auto")
    await daemon.start(project.id, [{"name": "builder", "text": "go"}])
    await _until(lambda: not daemon.is_running(project.id))

    (Path(project.root) / "uv.lock").write_text("a newer lock")
    team_id = await daemon.start(project.id, [{"name": "builder", "text": "again"}])
    await _until(lambda: not daemon.is_running(project.id))

    started = [
        e for e in _journal(project.root, team_id) if isinstance(e, EnvironmentPrepareStarted)
    ]
    assert [s.reason for s in started] == ["uv.lock changed since the last install"]
    assert len(calls.read_text().splitlines()) == 2


async def test_auto_installs_without_being_asked_and_off_and_skip_never_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kopicode(tmp_path, monkeypatch)
    calls = _uv_shim(tmp_path, monkeypatch, _MAKE_VENV)
    daemon = _daemon(tmp_path)
    project = _project(tmp_path, daemon, setting="off")

    await daemon.start(project.id, [{"name": "builder", "text": "go"}])  # off: never installs
    await _until(lambda: not daemon.is_running(project.id))
    daemon.projects.update_env_prepare(project.id, "auto")
    await daemon.start(project.id, [{"name": "builder", "text": "go"}], prepare="skip")
    await _until(lambda: not daemon.is_running(project.id))
    assert not calls.exists()

    await daemon.start(project.id, [{"name": "builder", "text": "go"}])  # auto: installs
    await _until(lambda: not daemon.is_running(project.id))
    assert calls.read_text().strip() == "sync --frozen"


async def test_a_failed_install_fails_every_role_with_why_and_starts_no_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kopicode(tmp_path, monkeypatch)
    _uv_shim(tmp_path, monkeypatch, 'echo "no matching distribution" >&2; exit 2')
    daemon = _daemon(tmp_path)
    project = _project(tmp_path, daemon, setting="auto")

    team_id = await daemon.start(
        project.id,
        [{"name": "builder", "text": "go"}, {"name": "reviewer", "text": "look"}],
    )
    await _until(lambda: not daemon.is_running(project.id))

    events = _journal(project.root, team_id)
    done = next(e for e in events if isinstance(e, EnvironmentPrepared))
    assert (done.ok, done.exit_code, done.failure) == (False, 2, "exit")
    assert "no matching distribution" in done.tail
    failed = [e for e in events if isinstance(e, TaskFailed)]
    assert sorted(e.role for e in failed if e.role) == ["builder", "reviewer"]
    assert "couldn't install the python dependencies" in failed[0].error
    assert "uv sync --frozen exited with code 2" in failed[0].error
    assert "no matching distribution" not in failed[0].error  # the output lives in the install row
    assert "prepare=skip" in failed[0].error
    assert not any(isinstance(e, DelegationStarted) for e in events)
    assert daemon.status(project.id) == {"builder": "failed", "reviewer": "failed"}
    assert envprep.read_state(project.root).get("python", {}).get("how") != "prepared"


async def test_after_a_failed_install_the_next_start_asks_again_not_goes_ahead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real-tool run found it: `uv venv` made a .venv, the install failed, and the half-made
    .venv then passed as installed, so the next start ran agents with nothing installed."""
    _fake_kopicode(tmp_path, monkeypatch)
    # Makes the .venv, then fails, like `uv venv` followed by a failing `uv pip install`.
    _uv_shim(tmp_path, monkeypatch, "mkdir -p .venv; touch .venv/pyvenv.cfg; exit 1")
    daemon = _daemon(tmp_path)
    project = _project(tmp_path, daemon, setting="auto")
    await daemon.start(project.id, [{"name": "builder", "text": "go"}])
    await _until(lambda: not daemon.is_running(project.id))
    assert (Path(project.root) / ".venv" / "pyvenv.cfg").exists()
    daemon.projects.update_env_prepare(project.id, "ask")

    with pytest.raises(EnvironmentConfirmationError, match="the last install did not finish"):
        await daemon.start(project.id, [{"name": "builder", "text": "again"}])


async def test_a_dependency_free_project_is_installed_once_not_on_every_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kopicode(tmp_path, monkeypatch)
    calls = _uv_shim(tmp_path, monkeypatch, "echo ok")  # succeeds and makes no .venv
    daemon = _daemon(tmp_path)
    project = _project(tmp_path, daemon)
    await daemon.start(project.id, [{"name": "builder", "text": "go"}], prepare="yes")
    await _until(lambda: not daemon.is_running(project.id))

    await daemon.start(project.id, [{"name": "builder", "text": "again"}])  # ask: must not ask
    await _until(lambda: not daemon.is_running(project.id))

    assert len(calls.read_text().splitlines()) == 1


async def test_stopping_while_installing_kills_the_install_and_ends_the_team(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kopicode(tmp_path, monkeypatch)
    _uv_shim(tmp_path, monkeypatch, "sleep 30")
    daemon = _daemon(tmp_path)
    project = _project(tmp_path, daemon, setting="auto")
    team_id = await daemon.start(project.id, [{"name": "builder", "text": "go"}])

    await _until(
        lambda: any(
            isinstance(e, EnvironmentPrepareStarted) for e in _journal(project.root, team_id)
        )
    )
    await daemon.stop(project.id)
    await _until(lambda: not daemon.is_running(project.id), seconds=20)

    events = _journal(project.root, team_id)
    done = next(e for e in events if isinstance(e, EnvironmentPrepared))
    assert (done.ok, done.failure) == (False, "cancelled")
    assert any(isinstance(e, TeamStopped) for e in events)
    assert not any(isinstance(e, DelegationStarted) for e in events)


# --- the HTTP surface -----------------------------------------------------------------------


def _client(tmp_path: Path) -> tuple[TestClient, FleetDaemon]:
    daemon = _daemon(tmp_path)
    app = create_app(daemon, security=satay.control.SecurityPolicy(token="test-token"))
    client = TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "test-token"})
    return client, daemon


def test_the_environment_route_carries_the_plan_and_the_setting(tmp_path: Path) -> None:
    client, daemon = _client(tmp_path)
    project = _project(tmp_path, daemon)

    body = client.get(f"/api/projects/{project.id}/environment").json()

    assert body["prepare"]["setting"] == "ask"
    (step,) = body["prepare"]["steps"]
    assert (step["name"], step["commands"], step["reason"]) == (
        "Python",
        [["uv", "sync", "--frozen"]],
        ".venv is missing",
    )
    assert body["prepare"]["unsupported"] == []


def test_the_setting_can_be_changed_and_is_validated(tmp_path: Path) -> None:
    client, daemon = _client(tmp_path)
    project = _project(tmp_path, daemon)
    url = f"/api/projects/{project.id}/environment"

    assert client.patch(url, json={"prepare": "sometimes"}).status_code == 400
    assert client.patch(url, json={}).status_code == 400
    body = client.patch(url, json={"prepare": "auto"}).json()

    assert body["env_prepare"] == "auto"
    assert client.get(f"/api/projects/{project.id}").json()["env_prepare"] == "auto"
    assert (
        client.patch("/api/projects/nope/environment", json={"prepare": "auto"}).status_code == 404
    )


def test_starting_an_ask_project_over_http_says_what_to_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kopicode(tmp_path, monkeypatch)
    calls = _uv_shim(tmp_path, monkeypatch, _MAKE_VENV)
    client, daemon = _client(tmp_path)
    project = _project(tmp_path, daemon)
    roles = [{"name": "builder", "text": "go"}]

    refused = client.post(f"/api/projects/{project.id}/start", json={"roles": roles})
    bad = client.post(
        f"/api/projects/{project.id}/start", json={"roles": roles, "prepare": "maybe"}
    )

    assert refused.status_code == 409
    assert "prepare=yes" in refused.json()["detail"] and "python" in refused.json()["detail"]
    assert bad.status_code == 400
    assert not calls.exists()


def test_an_existing_projects_database_without_the_column_opens_and_asks(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, root TEXT NOT NULL, "
        "secrets_scope TEXT NOT NULL, roles_json TEXT NOT NULL, last_team_id TEXT)"
    )
    connection.execute("INSERT INTO projects VALUES ('p1', 'old', '/r', 'old', '[]', NULL)")
    connection.commit()
    connection.close()

    store = ProjectStore.open(path)

    assert store.get("p1").env_prepare == "ask"
    store.close()


# --- what the agent is given (V5-E4) ----------------------------------------------------------


def _fake_log(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "sent.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _child_envs(tmp_path: Path) -> list[dict[str, Any]]:
    return [entry["child_env"] for entry in _fake_log(tmp_path) if "child_env" in entry]


async def test_the_agent_runs_in_the_projects_own_environment_and_without_cuttlefishs_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kopicode(tmp_path, monkeypatch)
    _uv_shim(tmp_path, monkeypatch, _MAKE_VENV)
    monkeypatch.setenv("E2B_API_KEY", "e2b_must_not_reach_the_agent")
    monkeypatch.setenv("CUTTLEFISH_SERVE_PASSWORD", "must-not-reach-the-agent")
    monkeypatch.setenv("SOME_AMBIENT_VAR", "ambient")
    daemon = _daemon(tmp_path)
    project = _project(tmp_path, daemon)

    await daemon.start(project.id, [{"name": "builder", "text": "go"}], prepare="yes")
    await _until(lambda: not daemon.is_running(project.id))

    (seen,) = _child_envs(tmp_path)
    venv = str(Path(project.root) / ".venv")
    assert seen["cwd"] == project.root
    assert seen["VIRTUAL_ENV"] == venv
    assert seen["PATH"].split(os.pathsep)[0] == f"{venv}/bin"
    assert "E2B_API_KEY" not in seen["names"] and "CUTTLEFISH_SERVE_PASSWORD" not in seen["names"]
    assert "SOME_AMBIENT_VAR" not in seen["names"]
    assert "HOME" in seen["names"] and "PATH" in seen["names"]
    assert "FAKE_KOPICODE_SCENARIO" in seen["names"]  # the operator's passthrough (conftest)


async def test_the_agents_brief_says_how_to_run_things_in_this_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kopicode(tmp_path, monkeypatch)
    _uv_shim(tmp_path, monkeypatch, _MAKE_VENV)
    daemon = _daemon(tmp_path)
    project = _project(tmp_path, daemon)

    await daemon.start(project.id, [{"name": "builder", "text": "fix the bug"}], prepare="yes")
    await _until(lambda: not daemon.is_running(project.id))

    prompts = [
        entry["params"]["prompt"]
        for entry in _fake_log(tmp_path)
        if entry.get("method") == "session.start"
    ]
    (prompt,) = prompts
    assert "Environment: Python: use the project's own environment" in prompt
    assert prompt.rstrip().endswith("fix the bug")  # the task still comes last


async def test_two_projects_get_their_own_agent_process_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resident `kopicode serve` child reads its environment once, so it is keyed by the
    project: one project's `.venv` must never be on another's agent's PATH."""
    _fake_kopicode(tmp_path, monkeypatch)
    _uv_shim(tmp_path, monkeypatch, _MAKE_VENV)
    daemon = _daemon(tmp_path)
    first = _project(tmp_path, daemon)
    second_root = tmp_path / "beta"
    second_root.mkdir()
    (second_root / "pyproject.toml").write_text('[project]\nname = "y"\n')
    (second_root / "uv.lock").write_text("lock")
    second = daemon.projects.register(name="beta", root=str(second_root))

    for project in (first, second):
        await daemon.start(project.id, [{"name": "builder", "text": "go"}], prepare="yes")
        await _until(lambda p=project: not daemon.is_running(p.id))

    seen = {env["cwd"]: env for env in _child_envs(tmp_path)}
    assert set(seen) == {first.root, second.root}
    assert seen[first.root]["VIRTUAL_ENV"] == f"{first.root}/.venv"
    assert seen[second.root]["VIRTUAL_ENV"] == f"{second.root}/.venv"
