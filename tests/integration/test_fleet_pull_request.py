"""Integration: the pull-request route (CUT-241, ADR-0049) over a real repository with a bare local
origin and a fake gh. Refusals are answers with a reason; the URL is journaled and read back."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import satay.control
from starlette.testclient import TestClient

from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.fleet.tiers import Tier, required_tier
from cuttlefish.projects.store import ProjectStore
from cuttlefish.sessions.manager import write_goal

if TYPE_CHECKING:
    from conftest import PrRig


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = ProjectStore.open(tmp_path / "projects.db")
    app = create_app(FleetDaemon(store), security=satay.control.SecurityPolicy(token="t"))
    with TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "t"}) as c:
        c.store = store  # type: ignore[attr-defined]
        yield c
    store.close()


def test_the_route_refuses_with_a_reason_then_pushes_opens_and_journals(
    client: TestClient, pr_rig: PrRig
) -> None:
    project = client.store.register(name="alpha", root=str(pr_rig.root))  # type: ignore[attr-defined]
    write_goal(
        str(pr_rig.root),
        "Add a summary command",
        "claude-code",
        branch="cuttlefish/add-summary",
        worktree=str(pr_rig.root),
    )
    path = f"/api/projects/{project.id}"
    assert client.get(f"{path}/goal").json()["goal"].get("pr_url") is None

    refused = client.post(f"{path}/pull-request", json={})
    assert refused.status_code == 200
    assert refused.json()["ok"] is False and refused.json()["code"] == "not_pushed"

    opened = client.post(f"{path}/pull-request", json={"push": True}).json()
    assert opened["ok"] is True and opened["url"] == "https://example.test/o/r/pull/9"
    assert "cuttlefish/add-summary" in pr_rig.git("-C", str(pr_rig.bare), "branch", "--list")
    assert client.get(f"{path}/goal").json()["goal"]["pr_url"] == opened["url"]
    timeline = client.get(f"{path}/overview").json()["timeline"]
    assert any(e["text"] == f"Pull request: {opened['url']}" for e in timeline)


def test_the_route_validates_its_body_and_the_project(client: TestClient) -> None:
    assert client.post("/api/projects/nope/pull-request", json={}).status_code == 404
    assert client.post("/api/projects/nope/pull-request", json={"push": "yes"}).status_code == 400


def test_opening_a_pull_request_needs_the_admin_tier() -> None:
    assert required_tier("POST", "/api/projects/{project_id}/pull-request") is Tier.ADMIN
