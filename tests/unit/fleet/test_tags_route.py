"""Project tags (CUT-191): the route, what it refuses, persistence and the Admin tier."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import satay.control
from starlette.testclient import TestClient

from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.fleet.tiers import Tier, required_tier
from cuttlefish.projects.store import ProjectStore, normalise_tags


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = ProjectStore.open(tmp_path / "projects.db")
    app = create_app(FleetDaemon(store), security=satay.control.SecurityPolicy(token="t"))
    with TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "t"}) as c:
        c.project_id = store.register(name="alpha", root=str(tmp_path)).id  # type: ignore[attr-defined]
        yield c
    store.close()


def test_tags_are_set_cleaned_listed_and_cleared(client: TestClient) -> None:
    path = f"/api/projects/{client.project_id}/tags"  # type: ignore[attr-defined]
    assert client.get(path.removesuffix("/tags")).json()["tags"] == []
    set_ = client.patch(path, json={"tags": [" client:acme ", "Rust", "rust", ""]})
    assert set_.status_code == 200 and set_.json()["tags"] == ["client:acme", "Rust"]
    listed = client.get("/api/projects").json()["projects"]
    assert listed[0]["tags"] == ["client:acme", "Rust"]
    assert client.patch(path, json={"tags": []}).json()["tags"] == []


def test_bad_tags_are_refused(client: TestClient) -> None:
    path = f"/api/projects/{client.project_id}/tags"  # type: ignore[attr-defined]
    assert client.patch(path, json={"tags": "acme"}).status_code == 400
    assert client.patch(path, json={"tags": [1]}).status_code == 400
    assert client.patch(path, json={"tags": ["x" * 25]}).status_code == 400
    assert client.patch(path, json={"tags": [f"t{i}" for i in range(11)]}).status_code == 400
    assert client.patch("/api/projects/nope/tags", json={"tags": []}).status_code == 404


def test_tags_persist_across_a_reopened_store(tmp_path: Path) -> None:
    store = ProjectStore.open(tmp_path / "p.db")
    project = store.register(name="a", root="/r")
    store.update_tags(project.id, ["one", "two"])
    store.close()
    again = ProjectStore.open(tmp_path / "p.db")
    assert again.get(project.id).tags == ("one", "two")
    again.close()


def test_normalise_keeps_the_first_spelling_and_collapses_spaces() -> None:
    assert normalise_tags(["Big  Client", "big client", " x"]) == ("Big Client", "x")


def test_changing_tags_needs_the_admin_tier() -> None:
    assert required_tier("PATCH", "/api/projects/p1/tags") is Tier.ADMIN
