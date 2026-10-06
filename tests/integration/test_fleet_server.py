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
from cuttlefish.fleet.fs import FolderBrowser
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
    assert body["roles"] == [
        {
            "name": "builder",
            "persona": "ships fast",
            "backend": None,
            "access": None,
            "default_prompt": False,
        }
    ]
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
    response = client.post(
        "/api/projects", json={"name": "demo", "root": str(tmp_path / "demo"), "roles": []}
    )
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


def test_usage_cost_is_null_not_zero_when_no_backend_reported_one(
    client: TestClient, tmp_path: Path
) -> None:
    """KAN-1810: a role with no reported dollar figure is "unknown", not "free"."""
    response = client.post(
        "/api/projects",
        json={"name": "demo", "root": str(tmp_path / "demo"), "roles": [{"name": "builder"}]},
    )
    assert response.json()["usage"]["builder"] == {"tokens": 0, "cost_usd": None}


def test_register_with_no_roles_gets_the_default_template(
    client: TestClient, tmp_path: Path
) -> None:
    body = client.post("/api/projects", json={"name": "demo", "root": str(tmp_path / "d")}).json()
    assert [r["name"] for r in body["roles"]] == ["builder", "reviewer"]
    assert [r["access"] for r in body["roles"]] == [None, "read-only"]
    assert all(r["default_prompt"] for r in body["roles"])


def test_register_with_a_template_gets_that_teams_roles(client: TestClient, tmp_path: Path) -> None:
    body = client.post(
        "/api/projects",
        json={"name": "demo", "root": str(tmp_path / "d"), "template": "full-crew"},
    ).json()
    assert [r["name"] for r in body["roles"]] == ["planner", "builder", "tester", "reviewer"]


def test_register_with_an_unknown_template_is_400(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/projects", json={"name": "demo", "root": str(tmp_path / "d"), "template": "nope"}
    )
    assert response.status_code == 400
    assert "solo-builder" in response.json()["detail"]


def test_explicit_empty_roles_stay_empty(client: TestClient, tmp_path: Path) -> None:
    body = client.post(
        "/api/projects", json={"name": "demo", "root": str(tmp_path / "d"), "roles": []}
    ).json()
    assert body["roles"] == []


def test_an_edited_builtin_is_no_longer_the_default_prompt(
    client: TestClient, tmp_path: Path
) -> None:
    created = client.post(
        "/api/projects", json={"name": "demo", "root": str(tmp_path / "d")}
    ).json()
    updated = client.patch(
        f"/api/projects/{created['id']}/roles",
        json={"roles": [{"name": "builder", "persona": "ship it"}]},
    ).json()
    assert updated["roles"][0]["default_prompt"] is False


def test_a_role_with_an_unknown_access_level_is_400(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/projects",
        json={"name": "d", "root": str(tmp_path / "d"), "roles": [{"name": "x", "access": "root"}]},
    )
    assert response.status_code == 400


def test_the_role_library_and_templates_are_served(client: TestClient) -> None:
    roles = client.get("/api/roles").json()["roles"]
    assert {r["name"] for r in roles} == {"builder", "reviewer", "tester", "planner", "docs-writer"}
    assert all(r["prompt"] for r in roles)
    templates = client.get("/api/templates").json()
    assert templates["default"] == "builder-reviewer"
    assert [t["name"] for t in templates["templates"]] == [
        "solo-builder",
        "builder-reviewer",
        "full-crew",
    ]


def test_a_project_defaults_to_standard_and_the_mode_can_be_set_at_register_and_patched(
    client: TestClient, tmp_path: Path
) -> None:
    plain = client.post("/api/projects", json={"name": "a", "root": str(tmp_path / "a")}).json()
    assert plain["mode"] == "standard"
    auto = client.post(
        "/api/projects", json={"name": "b", "root": str(tmp_path / "b"), "mode": "auto"}
    ).json()
    assert auto["mode"] == "auto"
    patched = client.patch(f"/api/projects/{plain['id']}/mode", json={"mode": "ask-first"}).json()
    assert patched["mode"] == "ask-first"
    assert client.get(f"/api/projects/{plain['id']}").json()["mode"] == "ask-first"


def test_an_unknown_mode_is_400_and_read_only_is_not_a_project_mode(
    client: TestClient, tmp_path: Path
) -> None:
    for bad in ("yolo", "read-only"):
        response = client.post(
            "/api/projects", json={"name": "a", "root": str(tmp_path / "a"), "mode": bad}
        )
        assert response.status_code == 400
    created = client.post("/api/projects", json={"name": "a", "root": str(tmp_path / "a")}).json()
    assert (
        client.patch(f"/api/projects/{created['id']}/mode", json={"mode": "x"}).status_code == 400
    )


def test_patching_the_mode_of_an_unknown_project_is_404(client: TestClient) -> None:
    assert client.patch("/api/projects/nope/mode", json={"mode": "auto"}).status_code == 404


def test_a_role_can_carry_an_explicit_access_override(client: TestClient, tmp_path: Path) -> None:
    body = client.post(
        "/api/projects",
        json={
            "name": "a",
            "root": str(tmp_path / "a"),
            "mode": "auto",
            "roles": [
                {"name": "r", "access": "read-only"},
                {"name": "s", "access": "standard"},
                {"name": "t"},
            ],
        },
    ).json()
    assert [r["access"] for r in body["roles"]] == ["read-only", "standard", None]


@pytest.fixture
def browsing_client(tmp_path: Path) -> TestClient:
    home = tmp_path / "home"
    (home / "proj" / ".git").mkdir(parents=True)
    (home / "plain").mkdir()
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    app = create_app(
        daemon,
        security=satay.control.SecurityPolicy(token="test-token"),
        folders=FolderBrowser([home]),
    )
    return TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "test-token"})


def test_the_folder_listing_is_served_with_its_roots(
    browsing_client: TestClient, tmp_path: Path
) -> None:
    body = browsing_client.get("/api/fs").json()
    assert body["root"] == str((tmp_path / "home").resolve())
    assert body["roots"] == [body["root"]]
    assert body["parent"] is None
    assert [(f["name"], f["is_git"]) for f in body["folders"]] == [("plain", False), ("proj", True)]


def test_a_subfolder_is_listed_by_path(browsing_client: TestClient, tmp_path: Path) -> None:
    path = str((tmp_path / "home" / "proj").resolve())
    body = browsing_client.get("/api/fs", params={"path": path}).json()
    assert body["path"] == path
    assert body["parent"] == str((tmp_path / "home").resolve())


def test_a_path_outside_the_roots_is_403_and_a_missing_one_404(
    browsing_client: TestClient, tmp_path: Path
) -> None:
    assert browsing_client.get("/api/fs", params={"path": "/etc"}).status_code == 403
    missing = str(tmp_path / "home" / "nope")
    assert browsing_client.get("/api/fs", params={"path": missing}).status_code == 404


def test_inspect_is_served_and_guarded(browsing_client: TestClient, tmp_path: Path) -> None:
    path = str((tmp_path / "home" / "plain").resolve())
    body = browsing_client.get("/api/fs/inspect", params={"path": path}).json()
    assert body["name"] == "plain" and body["is_git"] is False and body["languages"] == []
    assert browsing_client.get("/api/fs/inspect", params={"path": "/etc"}).status_code == 403
    assert browsing_client.get("/api/fs/inspect").status_code == 422


def test_the_folder_routes_need_the_token(tmp_path: Path) -> None:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    app = create_app(
        daemon,
        security=satay.control.SecurityPolicy(token="real"),
        folders=FolderBrowser([tmp_path]),
    )
    assert TestClient(app, base_url="http://127.0.0.1").get("/api/fs").status_code == 401


def test_a_daemon_with_no_folder_browser_answers_404(client: TestClient) -> None:
    assert client.get("/api/fs").status_code == 404
    assert client.get("/api/fs/inspect", params={"path": "/"}).status_code == 404
