"""Integration: the dashboard's secrets routes and what a team's start does with them (ADR-0006).

A real FastAPI app over a real `FleetDaemon`, a real encrypted store in a temp file. The rule under
test is write-only: a value goes in and is never in any response, the journal or a log line.
"""

from __future__ import annotations

import asyncio
import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
import satay.control
from starlette.testclient import TestClient

from cuttlefish import runtime
from cuttlefish.config import prepare_run
from cuttlefish.episodic.events import TaskSubmitted
from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.projects.store import ProjectStore
from cuttlefish.secrets.store import SecretsStore, generate_key

VALUE = "ghp_canary_value_0123456789"


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> str:
    value = generate_key()
    monkeypatch.setenv("CUTTLEFISH_SECRETS_KEY", value)
    return value


@pytest.fixture
def daemon(tmp_path: Path) -> FleetDaemon:
    (tmp_path / "demo").mkdir()
    (tmp_path / "other").mkdir()
    return FleetDaemon(ProjectStore.open(tmp_path / "projects.db"), secrets_db=tmp_path / "s.db")


@pytest.fixture
def client(daemon: FleetDaemon) -> TestClient:
    app = create_app(daemon, security=satay.control.SecurityPolicy(token="t"))
    return TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "t"})


def _project(client: TestClient, tmp_path: Path, name: str = "demo") -> str:
    created = client.post("/api/projects", json={"name": name, "root": str(tmp_path / name)})
    return str(created.json()["id"])


def test_without_a_key_the_list_says_off_and_a_write_is_409_with_how_to_fix_it(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CUTTLEFISH_SECRETS_KEY", raising=False)
    project = _project(client, tmp_path)
    assert client.get(f"/api/projects/{project}/secrets").json() == {
        "enabled": False,
        "project": [],
        "shared": [],
    }
    assert client.get("/api/secrets").json() == {"enabled": False, "shared": []}
    put = client.put(f"/api/projects/{project}/secrets/GITHUB_TOKEN", json={"value": VALUE})
    assert put.status_code == 409
    assert "cuttlefish secrets generate-key" in put.json()["detail"]
    assert VALUE not in put.text


def test_a_value_goes_in_and_never_comes_back(client: TestClient, tmp_path: Path, key: str) -> None:
    project = _project(client, tmp_path)
    put = client.put(f"/api/projects/{project}/secrets/GITHUB_TOKEN", json={"value": VALUE})
    assert put.status_code == 200 and put.json() == {"name": "GITHUB_TOKEN", "scope": "project"}
    listed = client.get(f"/api/projects/{project}/secrets")
    assert listed.json() == {
        "enabled": True,
        "project": [{"name": "GITHUB_TOKEN", "kind": "secret"}],
        "shared": [],
    }
    assert VALUE not in put.text + listed.text
    # It is stored, encrypted, under the project's scope.
    store = SecretsStore.open(tmp_path / "s.db", key=key)
    assert store.get("demo", "GITHUB_TOKEN") == VALUE
    raw = (tmp_path / "s.db").read_bytes()
    assert VALUE.encode() not in raw
    store.close()


def test_a_name_a_backend_runs_on_is_listed_as_a_credential(
    client: TestClient, tmp_path: Path, key: str
) -> None:
    project = _project(client, tmp_path)
    client.put(f"/api/projects/{project}/secrets/OPENROUTER_API_KEY", json={"value": VALUE})
    rows = client.get(f"/api/projects/{project}/secrets").json()["project"]
    assert rows == [{"name": "OPENROUTER_API_KEY", "kind": "credential"}]


def test_replace_and_remove(client: TestClient, tmp_path: Path, key: str) -> None:
    project = _project(client, tmp_path)
    path = f"/api/projects/{project}/secrets/GITHUB_TOKEN"
    client.put(path, json={"value": "first-value-aaaa"})
    client.put(path, json={"value": VALUE})
    store = SecretsStore.open(tmp_path / "s.db", key=key)
    assert store.get("demo", "GITHUB_TOKEN") == VALUE
    store.close()
    assert client.delete(path).status_code == 200
    assert client.get(f"/api/projects/{project}/secrets").json()["project"] == []
    assert client.delete(path).status_code == 404


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("github_token", {"value": VALUE}),
        ("GITHUB_TOKEN", {"value": ""}),
        ("GITHUB_TOKEN", {"value": 5}),
        ("GITHUB_TOKEN", {}),
    ],
)
def test_a_bad_name_or_value_is_400(
    client: TestClient, tmp_path: Path, key: str, name: str, body: dict[str, object]
) -> None:
    project = _project(client, tmp_path)
    response = client.put(f"/api/projects/{project}/secrets/{name}", json=body)
    assert response.status_code == 400
    assert VALUE not in response.text


def test_an_unknown_project_is_404(client: TestClient, key: str) -> None:
    assert client.get("/api/projects/nope/secrets").status_code == 404
    assert client.put("/api/projects/nope/secrets/X", json={"value": VALUE}).status_code == 404


def test_shared_secrets_are_listed_under_every_project_and_scoped_apart(
    client: TestClient, tmp_path: Path, key: str
) -> None:
    one = _project(client, tmp_path, "demo")
    two = _project(client, tmp_path, "other")
    assert client.put("/api/secrets/OPENROUTER_API_KEY", json={"value": VALUE}).status_code == 200
    client.put(f"/api/projects/{one}/secrets/GITHUB_TOKEN", json={"value": VALUE})
    for project in (one, two):
        shared = client.get(f"/api/projects/{project}/secrets").json()["shared"]
        assert shared == [{"name": "OPENROUTER_API_KEY", "kind": "credential"}]
    assert client.get(f"/api/projects/{two}/secrets").json()["project"] == []
    assert client.get("/api/secrets").json()["shared"] == shared
    assert client.delete("/api/secrets/OPENROUTER_API_KEY").status_code == 200
    assert client.delete("/api/secrets/OPENROUTER_API_KEY").status_code == 404


def test_a_team_start_hands_over_the_projects_own_and_the_shared_names_only(
    client: TestClient, daemon: FleetDaemon, tmp_path: Path, key: str
) -> None:
    one = _project(client, tmp_path, "demo")
    two = _project(client, tmp_path, "other")
    client.put(f"/api/projects/{one}/secrets/GITHUB_TOKEN", json={"value": VALUE})
    client.put(f"/api/projects/{two}/secrets/DB_PASSWORD", json={"value": VALUE})
    client.put("/api/secrets/NPM_TOKEN", json={"value": VALUE})
    assert daemon.project_secret_names(daemon.projects.get(one)) == ["GITHUB_TOKEN", "NPM_TOKEN"]
    assert daemon.project_secret_names(daemon.projects.get(two)) == ["DB_PASSWORD", "NPM_TOKEN"]


def test_a_project_secret_is_scrubbed_from_the_journal_and_a_folders_own_store_still_reads(
    tmp_path: Path, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CUTTLEFISH_KOPICODE_BIN", "true")
    root = tmp_path / "demo"
    (root / ".cuttlefish").mkdir(parents=True)
    central = SecretsStore.open(tmp_path / "s.db", key=key)
    central.set("demo", "GITHUB_TOKEN", VALUE)
    central.close()
    legacy = SecretsStore.open(root / ".cuttlefish" / "secrets.db", key=key)
    legacy.set("demo", "HF_TOKEN", "hf_legacy_value_9876543210")
    legacy.close()
    prepared = prepare_run(
        project="demo",
        secret_names=["GITHUB_TOKEN", "HF_TOKEN"],
        base_dir=root,
        central_secrets_db=tmp_path / "s.db",
    )
    try:
        assert prepared.secret_names == ("GITHUB_TOKEN", "HF_TOKEN")
        assert prepared.secrets_store is not None
        assert prepared.secrets_store.resolve("demo", ["HF_TOKEN"]) == {
            "HF_TOKEN": "hf_legacy_value_9876543210"
        }
        prepared.episodic_store.append(
            "t", TaskSubmitted(text=f"use {VALUE} and hf_legacy_value_9876543210")
        )
        journaled = "".join(str(e.payload) for e in prepared.episodic_store.read("t"))
        assert VALUE not in journaled and "hf_legacy_value_9876543210" not in journaled
        assert "[redacted:GITHUB_TOKEN]" in journaled
    finally:
        prepared.close()


FAKE = Path(__file__).parents[1] / "unit" / "delegate" / "fake_kopicode_serve.py"


@pytest.fixture
def _reset_runtime() -> Any:
    yield
    runtime.reset()


def _fake_kopicode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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
    session = [{"start": True}, {"emit": result}, {"close": [ended]}]
    (tmp_path / "scenario.json").write_text(json.dumps([*session, {"eof": []}]))
    log = tmp_path / "sent.jsonl"
    monkeypatch.setenv("FAKE_KOPICODE_SCENARIO", str(tmp_path / "scenario.json"))
    monkeypatch.setenv("FAKE_KOPICODE_LOG", str(log))
    binary = tmp_path / "kopicode"
    binary.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n')
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("CUTTLEFISH_KOPICODE_BIN", str(binary))
    monkeypatch.setenv("CUTTLEFISH_LLM_PROVIDER", "replay")
    return log


async def test_a_started_team_gets_the_projects_and_shared_secrets_as_environment_variables(
    tmp_path: Path, key: str, monkeypatch: pytest.MonkeyPatch, _reset_runtime: None
) -> None:
    log = _fake_kopicode(tmp_path, monkeypatch)
    (tmp_path / "demo").mkdir()
    (tmp_path / "other").mkdir()
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"), secrets_db=tmp_path / "s.db")
    project = daemon.projects.register(name="demo", root=str(tmp_path / "demo"))
    other = daemon.projects.register(name="other", root=str(tmp_path / "other"))
    daemon.set_secret(project.id, "GITHUB_TOKEN", VALUE)
    daemon.set_secret(other.id, "DB_PASSWORD", VALUE)
    daemon.set_secret(None, "NPM_TOKEN", VALUE)

    await daemon.start(project.id, [{"name": "builder", "text": "go"}])
    for _ in range(600):
        if not daemon.is_running(project.id):
            break
        await asyncio.sleep(0.05)
    lines = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    names = next(m["child_env"]["names"] for m in lines if "child_env" in m)
    assert "GITHUB_TOKEN" in names and "NPM_TOKEN" in names
    assert "DB_PASSWORD" not in names  # another project's secret
    journal = (tmp_path / "demo" / ".cuttlefish" / "episodic.db").read_bytes()
    assert VALUE.encode() not in journal
    assert not (tmp_path / "demo" / ".cuttlefish" / "secrets.db").exists()
