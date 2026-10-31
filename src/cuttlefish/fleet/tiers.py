"""What each paired device may do, per route (ADR-0037, ADR-0039).

A device reaching the daemon through the relay has a tier. The PC's own dashboard (the loopback
token or the password session) is the owner and is not limited by tiers. A tier includes every
lower one.

`ROUTE_TIERS` names the tier every route needs. A route that is not listed needs ADMIN, so
adding a route and forgetting it fails closed, and `tests/unit/fleet/test_tiers.py` fails too,
listing it."""

from __future__ import annotations

import re
from enum import IntEnum


class Tier(IntEnum):
    VIEW = 1
    APPROVE = 2
    TERMINAL = 3
    ADMIN = 4

    @property
    def label(self) -> str:
        return self.name.lower()


#: What a new device gets (ADR-0037, decided by Jian 2026-10-17).
DEFAULT_TIER = Tier.APPROVE


def parse_tier(name: str) -> Tier:
    try:
        return Tier[name.upper()]
    except KeyError:
        raise ValueError(f"unknown tier {name!r}; one of view, approve, terminal, admin") from None


#: Routes that need no credential at all, so no tier either.
PUBLIC = None

#: `(method, path template)` to the tier it needs. `WS` is a websocket.
ROUTE_TIERS: dict[tuple[str, str], Tier | None] = {
    ("GET", "/api/auth-mode"): PUBLIC,
    ("POST", "/api/login"): PUBLIC,
    # A session's hook script, with a token of its own that opens only this route (ADR-0040).
    ("POST", "/api/hooks/claude-code/{project_id}"): PUBLIC,
    # Read.
    ("GET", "/api/limits"): Tier.VIEW,
    ("GET", "/api/permissions"): Tier.VIEW,
    ("GET", "/api/roles"): Tier.VIEW,
    ("GET", "/api/templates"): Tier.VIEW,
    ("GET", "/api/projects"): Tier.VIEW,
    ("GET", "/api/projects/{project_id}"): Tier.VIEW,
    ("GET", "/api/projects/{project_id}/agent-session"): Tier.VIEW,
    ("GET", "/api/projects/{project_id}/goal"): Tier.VIEW,
    ("GET", "/api/projects/{project_id}/overview"): Tier.VIEW,
    ("GET", "/api/projects/{project_id}/digest"): Tier.VIEW,
    ("GET", "/api/projects/{project_id}/autopilot"): Tier.VIEW,
    ("GET", "/api/projects/{project_id}/artifacts"): Tier.VIEW,
    ("GET", "/api/projects/{project_id}/artifacts/file"): Tier.VIEW,
    ("GET", "/api/projects/{project_id}/environment"): Tier.VIEW,
    ("GET", "/api/projects/{project_id}/events"): Tier.VIEW,
    ("GET", "/api/projects/{project_id}/requests"): Tier.VIEW,
    ("GET", "/api/requests"): Tier.VIEW,
    ("GET", "/api/relay"): Tier.VIEW,
    # Answer, steer, start and stop.
    # The conversation itself: what the agent was asked and what its tools printed.
    ("GET", "/api/projects/{project_id}/chat"): Tier.APPROVE,
    ("POST", "/api/projects/{project_id}/approve"): Tier.APPROVE,
    ("POST", "/api/projects/{project_id}/requests/{request_id}/answer"): Tier.APPROVE,
    ("POST", "/api/projects/{project_id}/steer"): Tier.APPROVE,
    ("POST", "/api/projects/{project_id}/decisions"): Tier.APPROVE,
    ("POST", "/api/projects/{project_id}/start"): Tier.APPROVE,
    ("POST", "/api/projects/{project_id}/stop"): Tier.APPROVE,
    ("POST", "/api/projects/{project_id}/goal"): Tier.APPROVE,
    ("POST", "/api/projects/{project_id}/autopilot/{action}"): Tier.APPROVE,
    ("POST", "/api/projects/{project_id}/agent-session/start"): Tier.APPROVE,
    ("POST", "/api/projects/{project_id}/agent-session/stop"): Tier.APPROVE,
    ("GET", "/api/devices"): Tier.APPROVE,
    ("DELETE", "/api/devices/{device_id}"): Tier.APPROVE,  # a lost phone is cut off from another
    # The agent's terminal.
    ("WS", "/api/projects/{project_id}/terminal"): Tier.TERMINAL,
    # Typing into the agent's session from the chat box: the same typed access.
    ("POST", "/api/projects/{project_id}/chat"): Tier.TERMINAL,
    # Settings, secrets, folders, projects and devices.
    ("GET", "/api/fs"): Tier.ADMIN,
    ("GET", "/api/fs/inspect"): Tier.ADMIN,
    ("POST", "/api/projects"): Tier.ADMIN,
    ("DELETE", "/api/projects/{project_id}"): Tier.ADMIN,
    ("PATCH", "/api/projects/{project_id}/allow"): Tier.ADMIN,
    ("PATCH", "/api/projects/{project_id}/budget"): Tier.ADMIN,
    ("PATCH", "/api/projects/{project_id}/environment"): Tier.ADMIN,
    ("PATCH", "/api/projects/{project_id}/limits"): Tier.ADMIN,
    ("PATCH", "/api/projects/{project_id}/mode"): Tier.ADMIN,
    ("PATCH", "/api/projects/{project_id}/presets"): Tier.ADMIN,
    ("PATCH", "/api/projects/{project_id}/terminal-command"): Tier.ADMIN,
    ("PATCH", "/api/projects/{project_id}/worktree-per-goal"): Tier.ADMIN,
    ("POST", "/api/projects/{project_id}/pull-request"): Tier.ADMIN,  # leaves the machine (gh)
    ("PATCH", "/api/projects/{project_id}/roles"): Tier.ADMIN,
    ("GET", "/api/projects/{project_id}/secrets"): Tier.ADMIN,
    ("PUT", "/api/projects/{project_id}/secrets/{name}"): Tier.ADMIN,
    ("DELETE", "/api/projects/{project_id}/secrets/{name}"): Tier.ADMIN,
    ("GET", "/api/secrets"): Tier.ADMIN,
    ("PUT", "/api/secrets/{name}"): Tier.ADMIN,
    ("DELETE", "/api/secrets/{name}"): Tier.ADMIN,
    ("POST", "/api/devices/pairing"): Tier.ADMIN,
    ("DELETE", "/api/devices/pairing"): Tier.ADMIN,
    ("POST", "/api/devices/pending/{pending_id}/confirm"): Tier.ADMIN,
    ("POST", "/api/devices/pending/{pending_id}/reject"): Tier.ADMIN,
    ("PATCH", "/api/devices/{device_id}"): Tier.ADMIN,
}


def _pattern(template: str) -> re.Pattern[str]:
    return re.compile(
        "^"
        + re.sub(r"\{[^/]+\}", "[^/]+", re.escape(template).replace(r"\{", "{").replace(r"\}", "}"))
        + "$"
    )


# A literal segment must beat a placeholder (`/api/devices/pairing` over
# `/api/devices/{device_id}`), so the most specific template is tried first.
_COMPILED = sorted(
    ((method, template, _pattern(template)) for (method, template) in ROUTE_TIERS),
    key=lambda row: (row[1].count("{"), -len(row[1])),
)


def _route(method: str, path: str) -> tuple[str, str] | None:
    """The `ROUTE_TIERS` key a request matches, most specific template first."""
    for known, template, pattern in _COMPILED:
        if known == method.upper() and pattern.match(path):
            return known, template
    return None


def required_tier(method: str, path: str) -> Tier | None:
    """The tier a request needs, or `None` for a path that is not the API (the dashboard's own
    files). An API route with no entry in the table needs ADMIN."""
    if not path.startswith("/api/"):
        return None
    route = _route(method, path)
    return ROUTE_TIERS[route] if route else Tier.ADMIN


#: The routes the manager credential may reach (ADR-0046, ADR-0050): what its MCP tools call, and
#: nothing more. It is held to APPROVE as well, so every route here must need APPROVE or less (a
#: test checks it); the list narrows it further, because APPROVE alone would let it revoke a
#: paired device or start and stop teams.
MANAGER_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/projects"),
        ("GET", "/api/projects/{project_id}"),
        ("GET", "/api/projects/{project_id}/digest"),
        ("GET", "/api/requests"),
        ("GET", "/api/projects/{project_id}/requests"),
        ("POST", "/api/projects/{project_id}/requests/{request_id}/answer"),
        ("POST", "/api/projects/{project_id}/steer"),
        ("POST", "/api/projects/{project_id}/decisions"),
    }
)


def manager_may(method: str, path: str) -> bool:
    """Whether the manager credential may make this request."""
    route = _route(method, path)
    return route in MANAGER_ROUTES
