"""Unit: every route the daemon serves has a tier (CUT-120, ADR-0039). A route added without one
fails here, by name, instead of quietly needing admin (which is what it would get at run time)."""

from __future__ import annotations

from pathlib import Path

import pytest
import satay.control
from fastapi.routing import APIRoute, APIWebSocketRoute

from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.server import create_app
from cuttlefish.fleet.tiers import ROUTE_TIERS, Tier, parse_tier, required_tier
from cuttlefish.projects.store import ProjectStore


def _routes(tmp_path: Path) -> set[tuple[str, str]]:
    from cuttlefish.fleet.relay_link import RelayLink, RelaySettings

    link = RelayLink.create(RelaySettings("ws://127.0.0.1:1", tmp_path / "key"))
    app = create_app(
        FleetDaemon(ProjectStore.open(tmp_path / "p.db")),
        security=satay.control.SecurityPolicy(token="t"),
        relay=link,
    )
    link.close()
    found: set[tuple[str, str]] = set()
    for route in app.routes:
        if isinstance(route, APIRoute):
            found.update((m, route.path) for m in route.methods if m not in {"HEAD", "OPTIONS"})
        elif isinstance(route, APIWebSocketRoute):
            found.add(("WS", route.path))
    return {(m, p) for m, p in found if p.startswith("/api/")}


def test_every_api_route_has_a_tier(tmp_path: Path) -> None:
    missing = sorted(_routes(tmp_path) - set(ROUTE_TIERS))
    assert missing == [], f"add these to fleet/tiers.py ROUTE_TIERS: {missing}"


def test_no_tier_is_listed_for_a_route_that_does_not_exist(tmp_path: Path) -> None:
    stale = sorted(set(ROUTE_TIERS) - _routes(tmp_path))
    assert stale == [], f"remove these from ROUTE_TIERS: {stale}"


@pytest.mark.parametrize(
    ("method", "path", "tier"),
    [
        ("GET", "/api/projects", Tier.VIEW),
        ("GET", "/api/projects/abc", Tier.VIEW),
        ("POST", "/api/projects/abc/steer", Tier.APPROVE),
        ("WS", "/api/projects/abc/terminal", Tier.TERMINAL),
        ("PATCH", "/api/projects/abc/mode", Tier.ADMIN),
        ("DELETE", "/api/devices/pairing", Tier.ADMIN),  # the literal beats the placeholder
        ("DELETE", "/api/devices/0123", Tier.APPROVE),
        ("PATCH", "/api/devices/0123", Tier.ADMIN),
        ("PUT", "/api/projects/p/secrets/K", Tier.ADMIN),
        ("GET", "/api/not-a-route", Tier.ADMIN),  # an unlisted API route fails closed
        ("get", "/api/projects", Tier.VIEW),
        ("GET", "/api/projects/abc/", Tier.ADMIN),  # a trailing slash is not a listed route
    ],
)
def test_required_tier(method: str, path: str, tier: Tier) -> None:
    assert required_tier(method, path) == tier


def test_the_dashboards_own_files_and_the_public_routes_need_no_tier() -> None:
    assert required_tier("GET", "/") is None
    assert required_tier("GET", "/assets/index.js") is None
    assert required_tier("GET", "/api/auth-mode") is None
    assert required_tier("POST", "/api/login") is None


def test_tiers_are_ordered_and_parse_by_name() -> None:
    assert Tier.VIEW < Tier.APPROVE < Tier.TERMINAL < Tier.ADMIN
    assert parse_tier("Approve") is Tier.APPROVE
    with pytest.raises(ValueError):
        parse_tier("root")
