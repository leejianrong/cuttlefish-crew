"""An MCP server wrapping the fleet daemon's own HTTP API (KAN-1764, ADR-0020).

cuttlefish already has a CLI that's fully agent-drivable via shell (any agent
can call ``cuttlefish run``/``run-team``/``approve``/``steer`` today) — the
real gap this closes is for MCP-native hosts (Claude Code, Claude Desktop,
other MCP clients) that prefer typed tool calls over shelling out. This is a
thin wrapper, not a second product: every tool here maps close to 1:1 onto
one of ``cuttlefish.fleet.server``'s own ``/api/...`` routes, making one
``urllib`` request per call the exact same way ``cuttlefish.steering``'s own
HTTP client already does for satay's control API — no new wire protocol, no
business logic duplicated from the daemon.

**This process is a separate client of an already-running ``cuttlefish
serve``**, not the daemon itself — it needs that daemon's own ``base_url``
and a valid ``x-cuttlefish-token`` (the loopback default's printed-once
static token, or a non-loopback ``SessionAuth`` session token from
``POST /api/login``, ADR-0011), exactly the same credential the dashboard
already authenticates with. MCP's own auth model doesn't need a *new*
mechanism to compose with ADR-0011's — this process just holds and forwards
whichever token it was given, the identical bearer-token pattern
``FleetClient`` (``frontend/src/lib/api.ts``) already uses. Reusing a
non-loopback ``SessionAuth`` token specifically has one real, named limit
worth stating: that token expires (``DEFAULT_SESSION_TTL_SECONDS``, 12 hours)
and this server has no login flow of its own to refresh it — a long-lived
MCP server process against a non-loopback daemon needs a fresh token handed
to it (a restart) once the old one lapses. The loopback default's own static
token never expires, so this doesn't apply to the common single-operator
case at all.

A deliberately smaller surface than the daemon's full HTTP API — no
``update_roles``/``update_allow``/``update_budget``/``deregister``
equivalents this slice. Those are slower-moving, review-once configuration
actions (Q53's own "reviewed once, not retyped per call" precedent), not the
run/steer/approve workflow verbs an agent actually drives moment to moment;
every tool here is genuinely something an agent needs mid-task, not project
setup. A smaller surface is also a smaller thing to keep in sync with the
HTTP API as it evolves — this module's own named tradeoff (KAN-1764's own
card).
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

from mcp.server.mcpserver import MCPServer


class FleetApiError(Exception):
    """The fleet daemon was reached but rejected the request (a non-2xx response)."""


class FleetUnreachableError(Exception):
    """Couldn't reach the fleet daemon at all."""


def _request(
    base_url: str, token: str, method: str, path: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """One blocking, synchronous HTTP round trip to `{base_url}{path}` -- the
    identical shape `cuttlefish.steering`'s own `send_steering_message`/
    `cancel_run` already use for satay's control API, now against the fleet
    daemon's own `/api/...` surface instead. Blocking by design, the same
    "a short-lived process's only job is this one request" posture those
    functions hold to -- run off the event loop via `asyncio.to_thread`
    wherever an async caller needs it (every tool below does)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={"content-type": "application/json", "x-cuttlefish-token": token},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise FleetApiError(f"{method} {path} -> {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise FleetUnreachableError(f"couldn't reach {base_url}: {exc}") from exc


def build_mcp_server(*, base_url: str, token: str) -> MCPServer:
    """An `MCPServer` whose tools are thin, authenticated wrappers over
    `base_url`'s own fleet daemon HTTP API. A tool that raises
    (`FleetApiError`/`FleetUnreachableError`) is turned into a normal MCP
    tool-call error by the SDK itself (`MCPServer.call_tool`'s own generic
    exception handling) -- nothing here needs to catch and reformat either."""
    server = MCPServer(
        name="cuttlefish-fleet",
        instructions=(
            "Drive a running cuttlefish-crew fleet daemon: list registered "
            "projects, register a new one, start or stop a team, steer or "
            "approve a still-running role, and read a project's own event log."
        ),
    )

    @server.tool()
    async def list_projects() -> dict[str, Any]:
        """Every registered project: its roles, running state, and per-role status."""
        return await asyncio.to_thread(_request, base_url, token, "GET", "/api/projects")

    @server.tool()
    async def get_project(project_id: str) -> dict[str, Any]:
        """One project's full detail: roles, status, usage, budget, allowed commands."""
        return await asyncio.to_thread(
            _request, base_url, token, "GET", f"/api/projects/{project_id}"
        )

    @server.tool()
    async def register_project(
        name: str, root: str, roles: list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        """Register a new project. `roles` is a list of {"name", "persona"} (persona optional)."""
        body: dict[str, Any] = {"name": name, "root": root}
        if roles:
            body["roles"] = roles
        return await asyncio.to_thread(_request, base_url, token, "POST", "/api/projects", body)

    @server.tool()
    async def start_project(
        project_id: str, roles: list[dict[str, str]], require_approval: bool = False
    ) -> dict[str, Any]:
        """Start project_id's team. `roles` is a list of {"name", "text"}
        (which registered role, and what to do). `require_approval` blocks each
        role's round from finalizing until approve_project decides it."""
        body = {"roles": roles, "require_approval": require_approval}
        return await asyncio.to_thread(
            _request, base_url, token, "POST", f"/api/projects/{project_id}/start", body
        )

    @server.tool()
    async def stop_project(project_id: str) -> dict[str, Any]:
        """Stop project_id's currently running team."""
        return await asyncio.to_thread(
            _request, base_url, token, "POST", f"/api/projects/{project_id}/stop"
        )

    @server.tool()
    async def steer_project(project_id: str, role: str, text: str) -> dict[str, Any]:
        """Redirect a still-running role's work at its next round boundary."""
        body = {"role": role, "text": text}
        return await asyncio.to_thread(
            _request, base_url, token, "POST", f"/api/projects/{project_id}/steer", body
        )

    @server.tool()
    async def approve_project(
        project_id: str, role: str, approved: bool, comment: str | None = None
    ) -> dict[str, Any]:
        """Approve or reject a role's just-finished round. `comment` is
        required when rejecting, optional when approving."""
        body = {"role": role, "approved": approved, "comment": comment}
        return await asyncio.to_thread(
            _request, base_url, token, "POST", f"/api/projects/{project_id}/approve", body
        )

    @server.tool()
    async def get_events(project_id: str) -> dict[str, Any]:
        """project_id's last team's full episodic record, in order -- the
        same journal `cuttlefish show` and the dashboard's own event log
        both read."""
        return await asyncio.to_thread(
            _request, base_url, token, "GET", f"/api/projects/{project_id}/events"
        )

    return server
