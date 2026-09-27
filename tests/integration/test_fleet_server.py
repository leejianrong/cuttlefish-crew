"""Integration: the fleet daemon's HTTP surface (ADR-0009) -- a real FastAPI app
over a real `FleetDaemon`/`ProjectStore`, exercised with Starlette's `TestClient`
(in-process, no real socket). Token auth (`x-cuttlefish-token`) is real, reusing
`satay.control.SecurityPolicy` directly rather than a stub.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import satay.control
from starlette.testclient import TestClient

from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.projects.store import ProjectStore


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    security = satay.control.SecurityPolicy(token="test-token")
    app = create_app(daemon, security=security)
    return TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "test-token"})


def test_missing_token_is_rejected(tmp_path: Path) -> None:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    security = satay.control.SecurityPolicy(token="real-token")
    app = create_app(daemon, security=security)
    response = TestClient(app, base_url="http://127.0.0.1").get("/api/projects")
    assert response.status_code == 401


def test_wrong_token_is_rejected(tmp_path: Path) -> None:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    security = satay.control.SecurityPolicy(token="real-token")
    app = create_app(daemon, security=security)
    response = TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "wrong"}).get(
        "/api/projects"
    )
    assert response.status_code == 401


def test_register_then_list_round_trips(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/projects",
        json={
            "name": "demo",
            "root": str(tmp_path / "demo"),
            "roles": [{"name": "builder", "persona": "ships fast"}],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "demo"
    assert body["roles"] == [{"name": "builder", "persona": "ships fast"}]
    assert body["running"] is False

    listing = client.get("/api/projects").json()
    assert [p["id"] for p in listing["projects"]] == [body["id"]]


def test_register_with_allow_round_trips(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/projects",
        json={
            "name": "demo",
            "root": str(tmp_path / "demo"),
            "allow": [["uv", "run", "pytest"]],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["allow"] == [["uv", "run", "pytest"]]

    fetched = client.get(f"/api/projects/{body['id']}").json()
    assert fetched["allow"] == [["uv", "run", "pytest"]]


def test_update_allow_replaces_the_whole_set(client: TestClient, tmp_path: Path) -> None:
    created = client.post(
        "/api/projects",
        json={"name": "demo", "root": str(tmp_path / "demo"), "allow": [["go", "test"]]},
    )
    project_id = created.json()["id"]

    response = client.patch(
        f"/api/projects/{project_id}/allow", json={"allow": [["uv", "run", "pytest"]]}
    )
    assert response.status_code == 200
    assert response.json()["allow"] == [["uv", "run", "pytest"]]


def test_update_allow_for_an_unknown_project_is_404(client: TestClient) -> None:
    response = client.patch("/api/projects/no-such-id/allow", json={"allow": []})
    assert response.status_code == 404


def test_register_with_budget_round_trips(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/projects",
        json={
            "name": "demo",
            "root": str(tmp_path / "demo"),
            "max_tokens": 50_000,
            "max_cost_usd": 5.0,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["budget"] == {"max_tokens": 50_000, "max_cost_usd": 5.0}

    fetched = client.get(f"/api/projects/{body['id']}").json()
    assert fetched["budget"] == {"max_tokens": 50_000, "max_cost_usd": 5.0}


def test_register_with_no_budget_defaults_to_no_ceiling(client: TestClient, tmp_path: Path) -> None:
    response = client.post("/api/projects", json={"name": "demo", "root": str(tmp_path / "demo")})
    body = response.json()
    assert body["budget"] == {"max_tokens": None, "max_cost_usd": None}
    assert body["usage"] == {}


def test_update_budget_replaces_both_fields(client: TestClient, tmp_path: Path) -> None:
    created = client.post(
        "/api/projects",
        json={"name": "demo", "root": str(tmp_path / "demo"), "max_tokens": 1000},
    )
    project_id = created.json()["id"]

    response = client.patch(
        f"/api/projects/{project_id}/budget",
        json={"max_tokens": 2000, "max_cost_usd": 1.5},
    )
    assert response.status_code == 200
    assert response.json()["budget"] == {"max_tokens": 2000, "max_cost_usd": 1.5}


def test_update_budget_for_an_unknown_project_is_404(client: TestClient) -> None:
    response = client.patch("/api/projects/no-such-id/budget", json={"max_tokens": 100})
    assert response.status_code == 404


def test_update_budget_with_a_non_numeric_max_tokens_is_400(
    client: TestClient, tmp_path: Path
) -> None:
    created = client.post("/api/projects", json={"name": "demo", "root": str(tmp_path / "demo")})
    project_id = created.json()["id"]

    response = client.patch(
        f"/api/projects/{project_id}/budget", json={"max_tokens": "not a number"}
    )
    assert response.status_code == 400


def test_register_requires_name_and_root(client: TestClient) -> None:
    response = client.post("/api/projects", json={"name": "demo"})
    assert response.status_code == 400


def test_events_for_a_project_with_no_team_yet_is_empty(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/api/projects", json={"name": "demo", "root": str(tmp_path / "demo")})
    project_id = created.json()["id"]

    response = client.get(f"/api/projects/{project_id}/events")
    assert response.status_code == 200
    assert response.json() == {"events": []}


def test_events_for_an_unknown_project_is_404(client: TestClient) -> None:
    assert client.get("/api/projects/no-such-id/events").status_code == 404


def test_a_browser_cors_preflight_from_a_loopback_origin_succeeds_without_a_token(
    tmp_path: Path,
) -> None:
    """A real browser's preflight `OPTIONS` never carries `x-cuttlefish-token` at
    all -- it must be answered by CORS middleware itself, not rejected by the same
    token check a real request goes through (ADR-0009)."""
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    app = create_app(daemon, security=satay.control.SecurityPolicy(token="real-token"))
    client = TestClient(app, base_url="http://127.0.0.1")

    response = client.options(
        "/api/projects",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": TOKEN_HEADER,
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


def test_a_cors_preflight_from_a_non_loopback_origin_is_rejected(tmp_path: Path) -> None:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    app = create_app(daemon, security=satay.control.SecurityPolicy(token="real-token"))
    client = TestClient(app, base_url="http://127.0.0.1")

    response = client.options(
        "/api/projects",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in response.headers


def test_get_unknown_project_is_404(client: TestClient) -> None:
    assert client.get("/api/projects/no-such-id").status_code == 404


def test_deregister_then_get_is_404(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/api/projects", json={"name": "demo", "root": str(tmp_path / "demo")})
    project_id = created.json()["id"]

    assert client.delete(f"/api/projects/{project_id}").status_code == 204
    assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_steer_a_project_with_no_running_team_is_409(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/api/projects", json={"name": "demo", "root": str(tmp_path / "demo")})
    project_id = created.json()["id"]

    response = client.post(
        f"/api/projects/{project_id}/steer", json={"role": "builder", "text": "hi"}
    )
    assert response.status_code == 409


def test_approve_a_project_with_no_running_team_is_409(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/api/projects", json={"name": "demo", "root": str(tmp_path / "demo")})
    project_id = created.json()["id"]

    response = client.post(
        f"/api/projects/{project_id}/approve", json={"role": "builder", "approved": True}
    )
    assert response.status_code == 409


def test_approve_with_no_body_is_400_not_500(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/api/projects", json={"name": "demo", "root": str(tmp_path / "demo")})
    project_id = created.json()["id"]

    response = client.post(f"/api/projects/{project_id}/approve", content=b"")
    assert response.status_code == 400


def test_approve_with_malformed_json_is_400_not_500(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/api/projects", json={"name": "demo", "root": str(tmp_path / "demo")})
    project_id = created.json()["id"]

    response = client.post(f"/api/projects/{project_id}/approve", content=b"not json at all")
    assert response.status_code == 400


def test_approve_with_no_role_is_400(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/api/projects", json={"name": "demo", "root": str(tmp_path / "demo")})
    project_id = created.json()["id"]

    response = client.post(f"/api/projects/{project_id}/approve", json={"approved": True})
    assert response.status_code == 400


def test_approve_with_a_non_bool_approved_is_400(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/api/projects", json={"name": "demo", "root": str(tmp_path / "demo")})
    project_id = created.json()["id"]

    response = client.post(
        f"/api/projects/{project_id}/approve", json={"role": "builder", "approved": "yes"}
    )
    assert response.status_code == 400


def test_rejecting_with_no_comment_is_400(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/api/projects", json={"name": "demo", "root": str(tmp_path / "demo")})
    project_id = created.json()["id"]

    response = client.post(
        f"/api/projects/{project_id}/approve", json={"role": "builder", "approved": False}
    )
    assert response.status_code == 400


def test_start_an_unregistered_project_is_404(client: TestClient) -> None:
    response = client.post(
        "/api/projects/no-such-id/start", json={"roles": [{"name": "builder", "text": "do it"}]}
    )
    assert response.status_code == 404


def test_start_with_no_roles_is_400(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/api/projects", json={"name": "demo", "root": str(tmp_path / "demo")})
    project_id = created.json()["id"]

    response = client.post(f"/api/projects/{project_id}/start", json={"roles": []})
    assert response.status_code == 400


def test_register_with_no_body_is_400_not_500(client: TestClient) -> None:
    # A bare `curl -X POST .../projects` with no body at all -- reproduced live,
    # this used to raise an unhandled JSONDecodeError (a raw 500).
    response = client.post("/api/projects", content=b"")
    assert response.status_code == 400


def test_register_with_malformed_json_is_400_not_500(client: TestClient) -> None:
    response = client.post("/api/projects", content=b"{not json")
    assert response.status_code == 400


def test_register_with_a_json_array_body_is_400_not_500(client: TestClient) -> None:
    # Valid JSON, but not an object -- `body.get(...)` would otherwise raise
    # AttributeError instead of the intended "missing 'name'/'root'" 400.
    response = client.post("/api/projects", content=b"[1, 2, 3]")
    assert response.status_code == 400


def test_start_with_no_body_is_400_not_500(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/api/projects", json={"name": "demo", "root": str(tmp_path / "demo")})
    project_id = created.json()["id"]

    response = client.post(f"/api/projects/{project_id}/start", content=b"")
    assert response.status_code == 400


def test_steer_with_no_body_is_400_not_500(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/api/projects", json={"name": "demo", "root": str(tmp_path / "demo")})
    project_id = created.json()["id"]

    response = client.post(f"/api/projects/{project_id}/steer", content=b"")
    assert response.status_code == 400


def test_steer_with_malformed_json_is_400_not_500(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/api/projects", json={"name": "demo", "root": str(tmp_path / "demo")})
    project_id = created.json()["id"]

    response = client.post(f"/api/projects/{project_id}/steer", content=b"not json at all")
    assert response.status_code == 400


def test_update_roles_with_no_body_is_400_not_500(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/api/projects", json={"name": "demo", "root": str(tmp_path / "demo")})
    project_id = created.json()["id"]

    response = client.patch(f"/api/projects/{project_id}/roles", content=b"")
    assert response.status_code == 400


def test_update_allow_with_no_body_is_400_not_500(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/api/projects", json={"name": "demo", "root": str(tmp_path / "demo")})
    project_id = created.json()["id"]

    response = client.patch(f"/api/projects/{project_id}/allow", content=b"")
    assert response.status_code == 400
