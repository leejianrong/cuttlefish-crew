"""The fleet daemon's HTTP surface (ADR-0009).

FastAPI -- no new dependency: `satay[studio]` already pulls in FastAPI/uvicorn for
`satay.control.run_app` (ADR-0046), and `cuttlefish run --steerable` already made
that pin unconditional (Q46), not an opt-in extra of cuttlefish's own.

Loopback-only bind and a per-session bearer token (`x-cuttlefish-token`) -- the same
posture satay's own control API holds (ADR-0014), reused directly via
`satay.control.SecurityPolicy`/`generate_token`/`ensure_loopback_bind` rather than
reimplemented, since this daemon is a strictly higher-value target than a single
task's own control API: it can steer *every* registered project at once.

A non-loopback bind (ADR-0011, KAN-1706) switches onto a completely separate guard,
`cuttlefish.fleet.auth.SessionAuth` -- `create_app` treats either through the
`SecurityCheck` protocol and never needs to know which one is active.

`dashboard_dir` (ADR-0012, KAN-1707) optionally mounts the dashboard's own
production build (`frontend/dist`, `npm run build`) as static files alongside the
JSON API, so `cuttlefish serve` alone is a complete, one-process way to see the
dashboard -- registered *after* every `/api/...` route so it only ever catches
what those don't, and deliberately outside `_check_security`: the static shell
carries no secret of its own, same-origin API calls it makes are gated exactly
as before.
"""

from __future__ import annotations

import dataclasses
import socket
from collections.abc import Awaitable, Callable, Collection
from pathlib import Path
from typing import Any

import satay.control
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from cuttlefish.config import ConfigError, validate_backend_name
from cuttlefish.episodic.store import EpisodicEvent
from cuttlefish.fleet.auth import SecurityCheck, SessionAuth
from cuttlefish.fleet.daemon import FleetDaemon, FleetError, RoleStart
from cuttlefish.permissions import ACCESS_LEVELS, DEFAULT_MODE, MODES
from cuttlefish.projects.store import ProjectNotFoundError, RoleDefinition
from cuttlefish.roles import (
    BUILTIN_ROLES,
    DEFAULT_TEMPLATE,
    TEMPLATES,
    UnknownTemplateError,
    is_default_prompt,
    template_roles,
)

#: Distinct from satay's own `x-satay-token` (ADR-0046) -- a fleet-daemon request
#: and a per-task steering request must never be confused for one another.
TOKEN_HEADER = "x-cuttlefish-token"

#: Arbitrary, unregistered with IANA -- an operator with a real conflict overrides
#: it with `--port`, the same escape hatch `satay dev`'s own default port has.
DEFAULT_FLEET_PORT = 8420

#: Reachable with no credential at all in either auth mode (ADR-0011): the
#: dashboard must be able to tell which mode is active, and log in, before it
#: has ever held a token or session.
_PUBLIC_PATHS = frozenset({"/api/auth-mode", "/api/login"})


def _project_json(daemon: FleetDaemon, project_id: str) -> dict[str, Any]:
    project = daemon.projects.get(project_id)
    return {
        "id": project.id,
        "name": project.name,
        "root": project.root,
        "secrets_scope": project.secrets_scope,
        "backend": project.backend,
        "mode": project.mode,
        "roles": [
            {
                "name": r.name,
                "persona": r.persona,
                "backend": r.backend,
                "access": r.access,
                "default_prompt": is_default_prompt(r),
            }
            for r in project.roles
        ],
        "last_team_id": project.last_team_id,
        "allow": [list(command) for command in project.allow],
        "running": daemon.is_running(project.id),
        "status": daemon.status(project.id),
        "budget": {"max_tokens": project.max_tokens, "max_cost_usd": project.max_cost_usd},
        "usage": {
            name: {"tokens": totals.tokens, "cost_usd": totals.cost_usd}
            for name, totals in daemon.usage(project.id).items()
        },
    }


def _event_json(event: EpisodicEvent) -> dict[str, Any]:
    return {
        "seq": event.seq,
        "ts": event.ts.isoformat(),
        "event_type": type(event.payload).__name__,
        "payload": dataclasses.asdict(event.payload),
    }


def _roles_from_body(body: dict[str, Any]) -> tuple[RoleDefinition, ...]:
    """The request's roles; with none given, the named ``template`` (V4-B), else none."""
    if not body.get("roles") and body.get("template"):
        try:
            return template_roles(str(body["template"]))
        except UnknownTemplateError as exc:
            raise HTTPException(400, str(exc)) from exc
    return tuple(
        RoleDefinition(
            name=r["name"],
            persona=r.get("persona", ""),
            backend=_backend_from(r.get("backend")),
            access=_access_from(r.get("access")),
        )
        for r in body.get("roles", [])
    )


def _access_from(value: Any) -> str | None:
    """A role's optional access level: unset inherits the project's mode (``None``)."""
    if value in (None, ""):
        return None
    if value not in ACCESS_LEVELS:
        raise HTTPException(400, f"'access' must be one of {', '.join(ACCESS_LEVELS)}")
    return str(value)


def _mode_from(value: Any) -> str:
    """A request's permission mode; unset is the default (standard)."""
    if value in (None, ""):
        return DEFAULT_MODE
    if value not in MODES:
        raise HTTPException(400, f"'mode' must be one of {', '.join(MODES)}")
    return str(value)


def _backend_from(value: Any) -> str | None:
    """A request's optional backend name (KAN-1809), validated the same way the
    environment variable is -- a typo is a 400 now, not a failed start later."""
    if value in (None, ""):
        return None
    try:
        return validate_backend_name(str(value), source="backend")
    except ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc


def _allow_from_body(body: dict[str, Any]) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(command) for command in body.get("allow", []))


def _budget_from_body(body: dict[str, Any]) -> tuple[int | None, float | None]:
    """`(max_tokens, max_cost_usd)` off a request body (KAN-1712/ADR-0017) -- a
    missing key means "unset" (`None`), the same "omitted means unset" reading
    every other optional field on this surface already gets."""
    max_tokens = body.get("max_tokens")
    if max_tokens is not None and not isinstance(max_tokens, int):
        raise HTTPException(400, "'max_tokens', if given, must be an integer")
    max_cost_usd = body.get("max_cost_usd")
    if max_cost_usd is not None and not isinstance(max_cost_usd, int | float):
        raise HTTPException(400, "'max_cost_usd', if given, must be a number")
    return max_tokens, float(max_cost_usd) if max_cost_usd is not None else None


async def _json_body(request: Request) -> dict[str, Any]:
    """`request`'s JSON body, or a clean 400 -- never the raw `JSONDecodeError`
    FastAPI would otherwise turn into an unhandled 500 (reproduced live: a bare
    ``curl -X POST .../start`` with no body at all). Every handler below already
    validates its own parsed fields with an explicit `HTTPException(400, ...)`;
    a missing or malformed body is just an earlier instance of the same bad-input
    case, not a server error.
    """
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(400, f"request body must be valid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "request body must be a JSON object")
    return body


def create_app(
    daemon: FleetDaemon,
    *,
    security: SecurityCheck,
    login: SessionAuth | None = None,
    cors_origins: Collection[str] = (),
    dashboard_dir: Path | None = None,
) -> FastAPI:
    """`login` is only non-`None` in non-loopback/password mode (ADR-0011) -- it
    both backs `POST /api/login` and doubles as `security` in that mode, since
    `SessionAuth` implements `SecurityCheck` itself. `cors_origins` is the
    operator's own `--allow-origin` list, added to the loopback-only regex below
    rather than replacing it. `dashboard_dir` (ADR-0012), if given, must already
    exist -- an explicit request for a build that isn't there is a startup error,
    not a silent skip (`run_daemon`'s own job to tell the two cases apart).
    """
    app = FastAPI(title="cuttlefish-crew fleet daemon")

    @app.middleware("http")
    async def _check_security(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Only `/api/...` ever needs a credential (ADR-0012) -- the dashboard's own
        # static build, mounted below at "/" when `dashboard_dir` is given, carries
        # no secret of its own; gating page-load itself would just be friction with
        # no security benefit, since every API call it then makes is still checked.
        path = request.url.path
        if path.startswith("/api/") and path not in _PUBLIC_PATHS:
            try:
                security.check(
                    token=request.headers.get(TOKEN_HEADER),
                    host=request.headers.get("host"),
                    origin=request.headers.get("origin"),
                )
            except satay.control.AuthError as exc:
                return JSONResponse(status_code=exc.status, content={"detail": exc.detail})
        return await call_next(request)

    # Registered *after* `_check_security` so it wraps *outside* it (Starlette
    # applies the most-recently-added middleware first) -- a browser's CORS
    # preflight `OPTIONS` never carries the token header at all, so it must be
    # answered by `CORSMiddleware` itself, before `_check_security` ever sees it,
    # or every cross-origin request would fail before the real one is even sent.
    # The dashboard frontend (`frontend/`) is a separate origin during development
    # (Vite's own dev server, a different port); CORS only lets a browser's JS
    # *read* the response -- `_check_security` above is still the actual auth
    # boundary. Loopback-only by default (ADR-0014); `cors_origins` (ADR-0011)
    # adds the operator's own explicit non-loopback allow-list on top -- never a
    # wildcard either way.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(127\.0\.0\.1|\[::1\]|localhost)(:\d+)?",
        allow_origins=sorted(cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/auth-mode")
    async def auth_mode() -> dict[str, str]:
        return {"mode": "password" if login is not None else "token"}

    @app.post("/api/login")
    async def login_route(request: Request) -> dict[str, Any]:
        if login is None:
            raise HTTPException(404, "password login is not enabled for this daemon")
        body = await _json_body(request)
        password = body.get("password")
        if not password:
            raise HTTPException(400, "'password' is required")
        try:
            token = login.login(password)
        except satay.control.AuthError as exc:
            raise HTTPException(exc.status, exc.detail) from exc
        return {"token": token, "expires_in": login.ttl_seconds}

    @app.get("/api/roles")
    async def list_builtin_roles() -> dict[str, Any]:
        return {
            "roles": [
                {
                    "name": role.name,
                    "summary": role.summary,
                    "prompt": role.prompt,
                    "access": role.access,
                }
                for role in BUILTIN_ROLES.values()
            ]
        }

    @app.get("/api/templates")
    async def list_templates() -> dict[str, Any]:
        return {
            "default": DEFAULT_TEMPLATE,
            "templates": [
                {
                    "name": template.name,
                    "title": template.title,
                    "summary": template.summary,
                    "roles": list(template.roles),
                }
                for template in TEMPLATES.values()
            ],
        }

    @app.get("/api/projects")
    async def list_projects() -> dict[str, Any]:
        return {"projects": [_project_json(daemon, p.id) for p in daemon.projects.list()]}

    @app.post("/api/projects", status_code=201)
    async def register_project(request: Request) -> dict[str, Any]:
        body = await _json_body(request)
        name, root = body.get("name"), body.get("root")
        if not name or not root:
            raise HTTPException(400, "'name' and 'root' are required")
        max_tokens, max_cost_usd = _budget_from_body(body)
        project = daemon.projects.register(
            name=name,
            root=root,
            secrets_scope=body.get("secrets_scope"),
            roles=_roles_from_body(body)
            or (() if "roles" in body else template_roles(DEFAULT_TEMPLATE)),
            allow=_allow_from_body(body),
            max_tokens=max_tokens,
            max_cost_usd=max_cost_usd,
            backend=_backend_from(body.get("backend")),
            mode=_mode_from(body.get("mode")),
        )
        return _project_json(daemon, project.id)

    @app.get("/api/projects/{project_id}")
    async def get_project(project_id: str) -> dict[str, Any]:
        try:
            return _project_json(daemon, project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/projects/{project_id}/events")
    async def get_events(project_id: str) -> dict[str, Any]:
        try:
            events = daemon.events(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"events": [_event_json(event) for event in events]}

    @app.patch("/api/projects/{project_id}/roles")
    async def update_roles(project_id: str, request: Request) -> dict[str, Any]:
        body = await _json_body(request)
        try:
            daemon.projects.update_roles(project_id, _roles_from_body(body))
            return _project_json(daemon, project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.patch("/api/projects/{project_id}/allow")
    async def update_allow(project_id: str, request: Request) -> dict[str, Any]:
        body = await _json_body(request)
        try:
            daemon.projects.update_allow(project_id, _allow_from_body(body))
            return _project_json(daemon, project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.patch("/api/projects/{project_id}/mode")
    async def update_mode(project_id: str, request: Request) -> dict[str, Any]:
        body = await _json_body(request)
        mode = _mode_from(body.get("mode"))
        try:
            daemon.projects.update_mode(project_id, mode)
            return _project_json(daemon, project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.patch("/api/projects/{project_id}/budget")
    async def update_budget(project_id: str, request: Request) -> dict[str, Any]:
        body = await _json_body(request)
        max_tokens, max_cost_usd = _budget_from_body(body)
        try:
            daemon.projects.update_budget(
                project_id, max_tokens=max_tokens, max_cost_usd=max_cost_usd
            )
            return _project_json(daemon, project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.delete("/api/projects/{project_id}", status_code=204)
    async def deregister_project(project_id: str) -> None:
        if not daemon.projects.deregister(project_id):
            raise HTTPException(404, f"project {project_id!r} not found")

    @app.post("/api/projects/{project_id}/start")
    async def start_project(project_id: str, request: Request) -> dict[str, Any]:
        body = await _json_body(request)
        roles: list[RoleStart] = [
            {"name": r["name"], "text": r["text"]} for r in body.get("roles", [])
        ]
        if not roles:
            raise HTTPException(400, "'roles' must have at least one {name, text}")
        require_approval = bool(body.get("require_approval", False))
        try:
            team_id = await daemon.start(project_id, roles, require_approval=require_approval)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except FleetError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"team_id": team_id}

    @app.post("/api/projects/{project_id}/stop")
    async def stop_project(project_id: str) -> dict[str, Any]:
        try:
            await daemon.stop(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except FleetError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"status": "stopping"}

    @app.post("/api/projects/{project_id}/steer")
    async def steer_project(project_id: str, request: Request) -> dict[str, Any]:
        body = await _json_body(request)
        role, text = body.get("role"), body.get("text")
        if not role or not text:
            raise HTTPException(400, "'role' and 'text' are required")
        try:
            await daemon.steer(project_id, role, text)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except FleetError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"status": "sent"}

    @app.post("/api/projects/{project_id}/approve")
    async def approve_project(project_id: str, request: Request) -> dict[str, Any]:
        body = await _json_body(request)
        role, approved = body.get("role"), body.get("approved")
        if not role or not isinstance(approved, bool):
            raise HTTPException(400, "'role' (str) and 'approved' (bool) are required")
        comment = body.get("comment")
        if comment is not None and not isinstance(comment, str):
            raise HTTPException(400, "'comment', if given, must be a string")
        if not approved and not comment:
            raise HTTPException(400, "a rejection needs a non-empty 'comment'")
        try:
            await daemon.approve(project_id, role, approved=approved, comment=comment)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except FleetError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"status": "sent"}

    if dashboard_dir is not None:
        # Registered last (ADR-0012): Starlette matches routes in registration
        # order, so every `/api/...` route above still wins over this catch-all
        # mount at "/" -- only a request none of them matched (the dashboard's
        # own `index.html`, its `assets/*.js`/`*.css`) ever reaches it.
        # `html=True` serves `index.html` for `/`; the dashboard has no
        # client-side router of its own yet (plain `$state`-driven view
        # switching in `App.svelte`), so no further SPA-fallback route is
        # needed -- there is no second URL a browser refresh could land on.
        app.mount("/", StaticFiles(directory=dashboard_dir, html=True), name="dashboard")

    return app


def _self_origin(host: str, port: int) -> str:
    """The origin a browser sees navigating directly to this bind (ADR-0013) --
    always trusted in a non-loopback bind, in addition to `--allow-origin` and
    the loopback carve-out. Safe against DNS rebinding for the same reason the
    loopback carve-out already is: rebinding forges which *address* a request
    reaches, never the `Origin` header itself (derived from the page's own
    domain, the attacker's, not the rebound target) -- an exact match here can
    only happen from a browser actually pointed at this daemon on purpose.
    """
    return f"http://{host}:{port}"


def find_free_port(host: str, preferred: int, *, attempts: int = 20) -> int:
    """The first free port at or after `preferred` on `host` -- a plain
    `socket.bind` probe, pure stdlib (dev-playbook guidance: don't depend on
    `lsof`/`nc` being installed just to avoid a port collision).

    `cuttlefish serve` is meant to be a one-command entry point; on a personal
    machine already running several projects side by side, a hardcoded default
    port that just fails when it's taken is exactly the friction that guidance
    warns against -- the fix is trying the next port automatically and printing
    which one it actually landed on, not asking the operator to remember
    `--port` every time. A probe-then-release check is inherently racy (another
    process could grab the port in between) -- uvicorn's own bind is still what
    actually decides; this only picks a good first guess.
    """
    for candidate in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, candidate))
            except OSError:
                continue
            return candidate
    raise RuntimeError(f"no free port found in [{preferred}, {preferred + attempts}) on {host!r}")


async def run_daemon(
    daemon: FleetDaemon,
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_FLEET_PORT,
    password: str | None = None,
    cors_origins: Collection[str] = (),
    dashboard_dir: Path | None = None,
) -> None:
    """Serve `daemon`'s HTTP surface until cancelled (`cuttlefish serve`).

    Loopback `host` (the default): unchanged from before ADR-0011 -- a fresh
    static token, printed once to stdout, the only place it's ever shown, the
    same posture `cuttlefish run --steerable` already holds for its own token.

    Non-loopback `host` (ADR-0011, KAN-1706): `password` (from
    `CUTTLEFISH_SERVE_PASSWORD`, the CLI's own job to read -- never accepted
    here as a plain argument) is required, and switches the whole daemon onto
    `cuttlefish.fleet.auth.SessionAuth` instead. Raises `ValueError` if `host` is
    non-loopback and `password` is missing, or if `password` is too weak
    (`SessionAuth`'s own `WeakPasswordError`) -- refusing to start rather than
    silently falling back to the loopback guard for a bind that isn't loopback.
    A browser navigating directly to `http://{host}:{port}` always works with
    no `--allow-origin` needed at all (`_self_origin`, ADR-0013) -- the
    recommended shape for `--tailscale` (this machine's own tailnet address,
    resolved by the CLI, not this function).

    `dashboard_dir` (ADR-0012, KAN-1707): the CLI's own job to resolve (an
    explicit `--dashboard-dir` or a silent, best-effort default) -- by the time
    it reaches here, non-`None` always means "serve this," so a directory with
    no `index.html` is a startup error, not a silent no-dashboard fallback.
    """
    if dashboard_dir is not None and not (dashboard_dir / "index.html").exists():
        raise ValueError(
            f"--dashboard-dir {dashboard_dir} has no index.html -- "
            "build it first (`make frontend-build` / `npm run build` in frontend/)"
        )
    resolved_port = find_free_port(host, port)
    login: SessionAuth | None = None
    security: SecurityCheck
    dashboard_note = "  (serving the dashboard build too)" if dashboard_dir is not None else ""
    if satay.control.is_loopback_host(host):
        satay.control.ensure_loopback_bind(host)
        token = satay.control.generate_token()
        security = satay.control.SecurityPolicy(token=token)
        # flush=True: a long-running daemon's stdout is commonly redirected to a
        # log file rather than a TTY, where Python fully buffers by default -- an
        # operator piping this to a file must still be able to read the token
        # immediately, not only once enough further output accumulates to flush.
        print(
            f"cuttlefish serve: http://{host}:{resolved_port}  {TOKEN_HEADER}: {token}"
            f"{dashboard_note}",
            flush=True,
        )
    else:
        if not password:
            raise ValueError(
                "binding a non-loopback host requires CUTTLEFISH_SERVE_PASSWORD "
                "(ADR-0011) -- refusing to expose the daemon with no real auth"
            )
        login = SessionAuth(
            password=password,
            allowed_origins=frozenset({*cors_origins, _self_origin(host, resolved_port)}),
        )
        security = login
        print(
            f"cuttlefish serve: http://{host}:{resolved_port}  "
            f"(non-loopback -- POST /api/login with CUTTLEFISH_SERVE_PASSWORD, ADR-0011)"
            f"{dashboard_note}",
            flush=True,
        )
    app = create_app(
        daemon,
        security=security,
        login=login,
        cors_origins=cors_origins,
        dashboard_dir=dashboard_dir,
    )
    config = uvicorn.Config(app, host=host, port=resolved_port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
