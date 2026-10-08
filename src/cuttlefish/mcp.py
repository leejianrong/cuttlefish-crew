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
``update_allow``/``update_budget``/``deregister`` equivalents. Those are
slower-moving, review-once configuration actions (Q53's own "reviewed once, not
retyped per call" precedent), not the run/steer/approve workflow verbs an agent
actually drives moment to moment. KAN-1883 added the V4 surface an agent needs to
set a project up and to unblock a waiting team: permission modes and role access,
the built-in roles and templates, and the Needs-you requests (ADR-0028). A smaller
surface is also a smaller thing to keep in sync with the HTTP API as it evolves —
this module's own named tradeoff (KAN-1764's own card).
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError


class FleetApiError(ToolError):
    """The fleet daemon was reached but rejected the request (a non-2xx response).

    A `ToolError`, not a plain `Exception`: the SDK reports a `ToolError`'s message to the
    client, but hides the message of anything else behind a bare "Error executing tool
    <name>" (and logs a long traceback). The daemon's reason is the useful part (ADR-0029).
    """


class FleetUnreachableError(ToolError):
    """Couldn't reach the fleet daemon at all."""


def _detail_text(raw: str) -> str:
    """The `detail` string out of a FastAPI error body (`{"detail": "..."}`), else the body."""
    try:
        detail = json.loads(raw).get("detail")
    except (ValueError, AttributeError):
        return raw
    return detail if isinstance(detail, str) else raw


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
        detail = _detail_text(exc.read().decode("utf-8", errors="replace"))
        raise FleetApiError(f"{method} {path} -> {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise FleetUnreachableError(f"couldn't reach {base_url}: {exc}") from exc


def build_mcp_server(*, base_url: str, token: str) -> MCPServer:
    """An `MCPServer` whose tools are thin, authenticated wrappers over
    `base_url`'s own fleet daemon HTTP API. A tool that raises
    (`FleetApiError`/`FleetUnreachableError`, both `ToolError`s) is turned into an MCP
    tool-call error whose text is the daemon's own reason; nothing here needs to catch and
    reformat either."""
    server = MCPServer(
        name="cuttlefish-fleet",
        instructions=(
            "Drive a running cuttlefish-crew fleet daemon: list registered "
            "projects, register a new one, start or stop a team, steer or "
            "approve a still-running role, read a project's own event log, set a "
            "project's permission mode and role access, and answer the commands a "
            "waiting kopicode agent is asking to run. Permission modes: ask-first, "
            "standard, auto. A role's access is its project's mode, a mode of its "
            "own, or read-only. Only kopicode can pause for a request; Claude Code "
            "refuses a command nothing approves, and Codex does not filter commands (its "
            "sandbox only limits where it can write). Read-only blocks edits on "
            "Claude Code and Codex but not on kopicode. The never-allowed list "
            "(sudo, a forced git push, a download piped into a shell, a write outside "
            "the project root) applies in every mode and no answer overrides it. "
            "Every change to a mode or roles applies the next time the team starts."
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
        name: str,
        root: str,
        roles: list[dict[str, str]] | None = None,
        mode: str | None = None,
        template: str | None = None,
    ) -> dict[str, Any]:
        """Register a new project. `roles` is a list of {"name", "persona", "access"}
        (persona and access optional; access is ask-first, standard, auto or read-only,
        and unset inherits the project's mode). `template` names a team template
        (see list_templates) used when no `roles` are given. `mode` is ask-first,
        standard (the default) or auto."""
        body: dict[str, Any] = {"name": name, "root": root}
        if roles:
            body["roles"] = roles
        if mode:
            body["mode"] = mode
        if template:
            body["template"] = template
        return await asyncio.to_thread(_request, base_url, token, "POST", "/api/projects", body)

    @server.tool()
    async def get_project_environment(project_id: str) -> dict[str, Any]:
        """What a project's own files say it needs to run its code: each ecosystem found at
        the project root (Python, Node, Go, Rust, Java, Ruby), its package tool, lockfile,
        the version it asks for, and whether its own install (``.venv``, ``node_modules``) is
        present. Read from files only; nothing is run, and subdirectories are not scanned."""
        return await asyncio.to_thread(
            _request, base_url, token, "GET", f"/api/projects/{project_id}/environment"
        )

    @server.tool()
    async def get_permissions() -> dict[str, Any]:
        """The permission modes, the command groups a project can switch on, the
        never-allowed list and the per-backend notes (who can pause for a request,
        where read-only can and cannot stop an edit). Read this before setting a mode."""
        return await asyncio.to_thread(_request, base_url, token, "GET", "/api/permissions")

    @server.tool()
    async def list_builtin_roles() -> dict[str, Any]:
        """The built-in roles (name, default prompt, default access) a project can use."""
        return await asyncio.to_thread(_request, base_url, token, "GET", "/api/roles")

    @server.tool()
    async def list_templates() -> dict[str, Any]:
        """The built-in team templates a project can be registered from."""
        return await asyncio.to_thread(_request, base_url, token, "GET", "/api/templates")

    @server.tool()
    async def set_project_mode(project_id: str, mode: str) -> dict[str, Any]:
        """Set project_id's permission mode: ask-first, standard or auto. Applies the
        next time the team starts, not to one already running. Auto lets every command
        run except the never-allowed list."""
        return await asyncio.to_thread(
            _request, base_url, token, "PATCH", f"/api/projects/{project_id}/mode", {"mode": mode}
        )

    @server.tool()
    async def update_roles(project_id: str, roles: list[dict[str, str]]) -> dict[str, Any]:
        """Replace project_id's whole role list. Each role is {"name", "persona", "access"}
        (access: ask-first, standard, auto or read-only; unset inherits the project's
        mode; "backend" may also be given). Roles left out are removed. Applies the next
        time the team starts. read-only does not stop edits on kopicode."""
        return await asyncio.to_thread(
            _request,
            base_url,
            token,
            "PATCH",
            f"/api/projects/{project_id}/roles",
            {"roles": roles},
        )

    @server.tool()
    async def start_project(
        project_id: str,
        roles: list[dict[str, str]],
        require_approval: bool = False,
        prepare: str | None = None,
    ) -> dict[str, Any]:
        """Start project_id's team. `roles` is a list of {"name", "text"}
        (which registered role, and what to do). `require_approval` blocks each
        role's round from finalizing until approve_project decides it. `prepare`
        answers "may cuttlefish install the project's dependencies first?" when
        get_project_environment shows some are missing or stale: "yes" installs them
        (it runs the project's own install scripts), "skip" starts without. Left out, the
        project's own setting decides, and a project set to ask refuses until you say."""
        body: dict[str, Any] = {"roles": roles, "require_approval": require_approval}
        if prepare is not None:
            body["prepare"] = prepare
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

    @server.tool()
    async def list_requests(project_id: str | None = None) -> dict[str, Any]:
        """The commands waiting for a person. With no project_id: every pending request
        across the fleet. With one: that project's `pending` requests, then its last 50
        `resolved`. Each pending request has request_id, role, detail (the command), why
        it stopped, the answers allowed, a suggested_rule and expires_in_s: it denies on
        its own when that runs out. A request of kind `blocked` has no answers and no
        expires_in_s: the agent was stopped because it kept failing on the project's
        environment (detail is its last failing output); fix that, then steer the role
        (`steer_project`) and the request ends. Only kopicode agents raise requests."""
        path = "/api/requests" if project_id is None else f"/api/projects/{project_id}/requests"
        return await asyncio.to_thread(_request, base_url, token, "GET", path)

    @server.tool()
    async def answer_request(
        project_id: str,
        request_id: str,
        answer: str,
        rule: list[str] | None = None,
        text: str | None = None,
    ) -> dict[str, Any]:
        """Answer a waiting request: allow_once, allow_always or deny. A request of kind
        `question` (an agent asking a person something) takes `answer` or `decline`; `answer`
        needs `text`, which is passed back to the agent as the person's reply, so only send
        what the operator said. The journal scrubs only known secret values from it, not
        anything that merely looks like a key, so never put a credential in an answer.
        A request of kind `blocked` takes no answer (it is refused with 422): use steer_project
        instead. Answering
        allow_once or allow_always lets the agent run a shell command, so it is as
        weighty as start_project; only do it for a command the operator would approve.
        `rule` is for allow_always only: the words a command must start with, which must
        be the start of the command that was asked (default: the request's
        suggested_rule). allow_always applies to the running team at once and is saved to
        the project's own commands. The never-allowed list still wins. Sending the same
        answer again is fine (`already` is true). A different answer, or one after the
        request expired, was cancelled or was abandoned by a restart, is a 409 naming how
        it ended; an unknown request is a 404; a refused rule is a 422 with the reason and
        the request stays pending."""
        body: dict[str, Any] = {"answer": answer}
        if rule is not None:
            body["rule"] = rule
        if text is not None:
            body["text"] = text
        return await asyncio.to_thread(
            _request,
            base_url,
            token,
            "POST",
            f"/api/projects/{project_id}/requests/{request_id}/answer",
            body,
        )

    return server
