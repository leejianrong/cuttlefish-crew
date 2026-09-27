"""Integration: `cuttlefish serve` optionally serving the dashboard's own
production build (ADR-0012, KAN-1707) -- a real FastAPI app with a real
`StaticFiles` mount over a throwaway build directory, exercised with
Starlette's `TestClient`.
"""

from __future__ import annotations

from pathlib import Path

import satay.control
from starlette.testclient import TestClient

from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.projects.store import ProjectStore


def _fake_dashboard_build(tmp_path: Path) -> Path:
    dashboard_dir = tmp_path / "dist"
    dashboard_dir.mkdir()
    (dashboard_dir / "index.html").write_text("<html><body>fake dashboard</body></html>")
    assets_dir = dashboard_dir / "assets"
    assets_dir.mkdir()
    (assets_dir / "index.js").write_text("console.log('fake bundle');")
    return dashboard_dir


def test_serves_index_html_at_the_root_with_no_credential(tmp_path: Path) -> None:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    security = satay.control.SecurityPolicy(token="test-token")
    app = create_app(daemon, security=security, dashboard_dir=_fake_dashboard_build(tmp_path))

    response = TestClient(app, base_url="http://127.0.0.1").get("/")

    assert response.status_code == 200
    assert "fake dashboard" in response.text


def test_serves_a_static_asset_with_no_credential(tmp_path: Path) -> None:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    security = satay.control.SecurityPolicy(token="test-token")
    app = create_app(daemon, security=security, dashboard_dir=_fake_dashboard_build(tmp_path))

    response = TestClient(app, base_url="http://127.0.0.1").get("/assets/index.js")

    assert response.status_code == 200
    assert "fake bundle" in response.text


def test_api_routes_still_require_a_token_even_with_a_dashboard_mounted(tmp_path: Path) -> None:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    security = satay.control.SecurityPolicy(token="test-token")
    app = create_app(daemon, security=security, dashboard_dir=_fake_dashboard_build(tmp_path))

    response = TestClient(app, base_url="http://127.0.0.1").get("/api/projects")

    assert response.status_code == 401


def test_api_routes_still_work_with_the_right_token_and_a_dashboard_mounted(
    tmp_path: Path,
) -> None:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    security = satay.control.SecurityPolicy(token="test-token")
    app = create_app(daemon, security=security, dashboard_dir=_fake_dashboard_build(tmp_path))

    response = TestClient(
        app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "test-token"}
    ).get("/api/projects")

    assert response.status_code == 200


def test_root_is_a_plain_404_with_no_dashboard_dir_configured(tmp_path: Path) -> None:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    security = satay.control.SecurityPolicy(token="test-token")
    app = create_app(daemon, security=security)

    response = TestClient(app, base_url="http://127.0.0.1").get("/")

    assert response.status_code == 404
