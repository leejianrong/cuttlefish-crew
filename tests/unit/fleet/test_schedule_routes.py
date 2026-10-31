"""The schedule routes (CUT-246): validation, round trip, and the Admin tier."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import satay.control
from starlette.testclient import TestClient

from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.fleet.tiers import Tier, required_tier
from cuttlefish.projects.schedules import ScheduleStore
from cuttlefish.projects.store import ProjectStore


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    path = tmp_path / "projects.db"
    store = ProjectStore.open(path)
    daemon = FleetDaemon(store, schedules=ScheduleStore.open(path))
    app = create_app(daemon, security=satay.control.SecurityPolicy(token="t"))
    with TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "t"}) as c:
        c.project_id = store.register(name="alpha", root=str(tmp_path)).id  # type: ignore[attr-defined]
        yield c
    store.close()


def test_add_list_pause_resume_delete(client: TestClient) -> None:
    base = f"/api/projects/{client.project_id}/schedules"  # type: ignore[attr-defined]
    made = client.post(base, json={"goal": " Triage ", "recurrence": "every 2h"})
    assert made.status_code == 200
    schedule = made.json()
    assert schedule["goal"] == "Triage" and schedule["enabled"] is True
    assert schedule["last_run"] is None and schedule["next_due"].endswith("+00:00")
    assert [s["id"] for s in client.get(base).json()["schedules"]] == [schedule["id"]]

    off = client.patch(f"/api/schedules/{schedule['id']}", json={"enabled": False})
    assert off.json()["enabled"] is False
    assert client.delete(f"/api/schedules/{schedule['id']}").status_code == 200
    assert client.get(base).json()["schedules"] == []


def test_bad_input_and_unknown_things(client: TestClient) -> None:
    base = f"/api/projects/{client.project_id}/schedules"  # type: ignore[attr-defined]
    assert client.post(base, json={"goal": "g", "recurrence": "weekly"}).status_code == 400
    assert client.post(base, json={"goal": " ", "recurrence": "every 1h"}).status_code == 400
    assert client.post(base, json={"goal": "g"}).status_code == 400
    assert (
        client.post(
            "/api/projects/nope/schedules", json={"goal": "g", "recurrence": "every 1h"}
        ).status_code
        == 404
    )
    assert client.get("/api/projects/nope/schedules").status_code == 404
    assert client.patch("/api/schedules/nope", json={"enabled": True}).status_code == 404
    assert client.patch("/api/schedules/nope", json={"enabled": "yes"}).status_code == 400
    assert client.delete("/api/schedules/nope").status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/projects/{project_id}/schedules"),
        ("POST", "/api/projects/{project_id}/schedules"),
        ("PATCH", "/api/schedules/{schedule_id}"),
        ("DELETE", "/api/schedules/{schedule_id}"),
    ],
)
def test_schedules_need_the_admin_tier(method: str, path: str) -> None:
    assert required_tier(method, path) is Tier.ADMIN
