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

import asyncio
import contextlib
import dataclasses
import json
import logging
import secrets
import socket
from collections.abc import Awaitable, Callable, Collection, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import satay.control
import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from cuttlefish import environment, envprep
from cuttlefish.artifacts import (
    ArtifactNotFoundError,
    ArtifactNotPreviewableError,
    ArtifactTooLargeError,
)
from cuttlefish.autopilot import Limits
from cuttlefish.config import ConfigError, validate_backend_name
from cuttlefish.delegate.consent import ConsentPolicyError, validate_allow_entry
from cuttlefish.delegate.never_allowed import NEVER_ALLOWED_SUMMARY
from cuttlefish.delegate.presets import (
    DEFAULT_PRESETS,
    PRESET_INFO,
    PRESETS,
    UnknownPresetError,
    validate_presets,
)
from cuttlefish.episodic.store import EpisodicEvent
from cuttlefish.fleet import hook_files
from cuttlefish.fleet.auth import SecurityCheck, SessionAuth
from cuttlefish.fleet.autopilot import AutopilotError
from cuttlefish.fleet.daemon import (
    FleetDaemon,
    FleetError,
    RoleStart,
    SecretsUnavailableError,
    UnknownRoleError,
)
from cuttlefish.fleet.devices import DeviceError
from cuttlefish.fleet.fs import FolderBrowser, NotAFolderError, OutsideBrowseRootsError
from cuttlefish.fleet.relay_link import DEVICE_HEADER, RelayLink, RelaySettings, TierRefusal
from cuttlefish.fleet.serve_token import load_or_create_token
from cuttlefish.fleet.tiers import DEFAULT_TIER, Tier, parse_tier
from cuttlefish.limits import LIMIT_SPECS, LimitsError, validate_limits
from cuttlefish.permissions import (
    ACCESS_LEVELS,
    DEFAULT_MODE,
    MODE_INFO,
    MODES,
    backend_notes,
)
from cuttlefish.projects.store import (
    Project,
    ProjectNotFoundError,
    ProjectRootError,
    ProjectRootInUseError,
    RoleDefinition,
    check_root,
    check_root_free,
)
from cuttlefish.requests import (
    AlreadyResolvedError,
    HistoryEntry,
    PendingRequest,
    RequestError,
)
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

#: A password-mode response carries a fresh session token here when the caller's is past half its
#: life (ADR-0038); the dashboard keeps it, so a phone in use stays signed in.
TOKEN_REFRESH_HEADER = "x-cuttlefish-token-refresh"

#: Arbitrary, unregistered with IANA -- an operator with a real conflict overrides
#: it with `--port`, the same escape hatch `satay dev`'s own default port has.
DEFAULT_FLEET_PORT = 8420

#: Reachable with no credential at all in either auth mode (ADR-0011): the
#: dashboard must be able to tell which mode is active, and log in, before it
#: has ever held a token or session.
_PUBLIC_PATHS = frozenset({"/api/auth-mode", "/api/login"})

#: The route a Claude Code session's hook script posts to, and the header that authenticates it. A
#: separate token from the owner's: it can raise a Needs you card and cannot answer one (ADR-0040).
HOOK_PREFIX = "/api/hooks/"
HOOK_TOKEN_HEADER = "x-cuttlefish-hook-token"
_HOOK_BODY_LIMIT = 256 * 1024

_LOG = logging.getLogger(__name__)


def _project_json(daemon: FleetDaemon, project_id: str) -> dict[str, Any]:
    project = daemon.projects.get(project_id)
    return {
        "id": project.id,
        "name": project.name,
        "root": project.root,
        "secrets_scope": project.secrets_scope,
        "backend": project.backend,
        "mode": project.mode,
        "env_prepare": project.env_prepare,
        "limits": dict(project.limits),
        "presets": list(project.presets if project.presets is not None else DEFAULT_PRESETS),
        "roles": [
            {
                "name": r.name,
                "persona": r.persona,
                "backend": r.backend,
                "access": r.access,
                "limits": dict(r.limits),
                "default_prompt": is_default_prompt(r),
            }
            for r in project.roles
        ],
        "last_team_id": project.last_team_id,
        # Whether the last team was started with a review gate: only then does a role that
        # is blocked after a round mean "waiting for your review" (the dashboard words it so).
        "require_approval": project.last_team_require_approval,
        "allow": [list(command) for command in project.allow],
        "running": daemon.is_running(project.id),
        "stopping": daemon.is_stopping(project.id),
        "status": daemon.status(project.id),
        "last_event_at": daemon.last_event_at(project.id),
        "budget": {"max_tokens": project.max_tokens, "max_cost_usd": project.max_cost_usd},
        "usage": {
            name: {"tokens": totals.tokens, "cost_usd": totals.cost_usd}
            for name, totals in daemon.usage(project.id).items()
        },
    }


def _pending_json(daemon: FleetDaemon, pending: PendingRequest) -> dict[str, Any]:
    """A pending request as the dashboard shows it: the journaled (redacted) record, never the
    agent's raw command line (ADR-0028)."""
    record = pending.record
    try:
        project_name = daemon.projects.get(pending.project_id).name
    except ProjectNotFoundError:
        project_name = pending.project_id
    return {
        **dataclasses.asdict(record),
        "id": record.request_id,
        "state": "pending",
        "project_id": pending.project_id,
        "project_name": project_name,
        "team_id": pending.team_id,
        # A `blocked` request has no deadline: nothing is waiting to be denied.
        "expires_in_s": (
            None
            if record.kind == "blocked"
            else max(0, round((pending.deadline - datetime.now(UTC)).total_seconds()))
        ),
    }


def _resolved_json(project: Project, entry: HistoryEntry) -> dict[str, Any]:
    resolved = entry.resolved
    assert resolved is not None
    return {
        **dataclasses.asdict(entry.raised),
        "id": entry.raised.request_id,
        "state": resolved.resolution,
        "by": resolved.by,
        "rule": resolved.rule,
        "text": resolved.text,
        "project_id": project.id,
        "project_name": project.name,
        "raised_at": entry.raised_at.isoformat(),
        "resolved_at": entry.resolved_at.isoformat() if entry.resolved_at else None,
    }


_ANSWER_RESOLUTION = {
    "allow_once": "allowed_once",
    "allow_always": "allowed_always",
    "deny": "denied",
    "answer": "answered",
    "decline": "declined",
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
            limits=_limits_from(r.get("limits")),
        )
        for r in body.get("roles", [])
    )


def _limits_from(value: Any) -> dict[str, int]:
    """A request's limits (ADR-0030): known keys, whole numbers in range, ``null`` inherits."""
    try:
        return validate_limits(value)
    except LimitsError as exc:
        raise HTTPException(400, str(exc)) from exc


def _access_from(value: Any) -> str | None:
    """A role's optional access level: unset inherits the project's mode (``None``)."""
    if value in (None, ""):
        return None
    if value not in ACCESS_LEVELS:
        raise HTTPException(400, f"'access' must be one of {', '.join(ACCESS_LEVELS)}")
    return str(value)


def _presets_from(value: Any) -> tuple[str, ...] | None:
    """A request's optional command groups; unset keeps the defaults (`None`)."""
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
        raise HTTPException(400, "'presets' must be a list of preset names")
    try:
        return validate_presets(value)
    except UnknownPresetError as exc:
        raise HTTPException(400, str(exc)) from exc


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
    """The project's own commands off a request body, each checked as ``ConsentPolicy`` will
    read it (ADR-0028), so a bad entry is a 400 now and not a refused delegation later."""
    raw = body.get("allow", [])
    if not isinstance(raw, list):
        raise HTTPException(400, "allow must be a list of commands")
    entries: list[tuple[str, ...]] = []
    for command in raw:
        if not (isinstance(command, list) and all(isinstance(word, str) for word in command)):
            raise HTTPException(400, "each allowed command must be a list of words")
        try:
            validate_allow_entry(command)
        except ConsentPolicyError as exc:
            raise HTTPException(400, f"{' '.join(command)!r}: {exc}") from exc
        entries.append(tuple(command))
    return tuple(entries)


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


def _int(value: object, default: int) -> int:
    """A positive terminal dimension off the wire, or ``default``."""
    return (
        value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default
    )


def _autopilot_limits(value: object) -> Limits | None:
    """Autopilot's limits off the wire (CUT-174): absent or false is off, true is the defaults,
    and an object sets any of ``hours``, ``tokens`` and ``nudges``. Anything else is a 400."""
    if value is None or value is False:
        return None
    if value is True:
        return Limits()
    if not isinstance(value, dict):
        raise HTTPException(400, "'autopilot' must be true, false or an object of limits")
    defaults = Limits()

    def number(key: str, low: float, high: float, default: float) -> float:
        raw = value.get(key, default)
        if isinstance(raw, bool) or not isinstance(raw, int | float) or not low <= raw <= high:
            raise HTTPException(400, f"autopilot '{key}' must be a number from {low:g} to {high:g}")
        return float(raw)

    return Limits(
        hours=number("hours", 0.1, 720, defaults.hours),
        tokens=int(number("tokens", 10_000, 1_000_000_000, defaults.tokens)),
        nudges=int(number("nudges", 1, 5000, defaults.nudges)),
    )


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
    folders: FolderBrowser | None = None,
    relay: RelayLink | None = None,
    hook_token: str | None = None,
) -> FastAPI:
    """`login` is only non-`None` in non-loopback/password mode (ADR-0011) -- it
    both backs `POST /api/login` and doubles as `security` in that mode, since
    `SessionAuth` implements `SecurityCheck` itself. `cors_origins` is the
    operator's own `--allow-origin` list, added to the loopback-only regex below
    rather than replacing it. `dashboard_dir` (ADR-0012), if given, must already
    exist -- an explicit request for a build that isn't there is a startup error,
    not a silent skip (`run_daemon`'s own job to tell the two cases apart).
    `folders` (V4-E, ADR-0026) backs the dashboard's folder picker; without one, those two
    routes answer 404 rather than browsing anything by default.
    """
    app = FastAPI(title="cuttlefish-crew fleet daemon")
    # Without one the hook route answers nothing, so a daemon built without hooks (a test of
    # something else) has no way in through it.
    hook_token = hook_token or secrets.token_urlsafe(32)

    @app.middleware("http")
    async def _check_security(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Only `/api/...` ever needs a credential (ADR-0012) -- the dashboard's own
        # static build, mounted below at "/" when `dashboard_dir` is given, carries
        # no secret of its own; gating page-load itself would just be friction with
        # no security benefit, since every API call it then makes is still checked.
        path = request.url.path
        if path.startswith(HOOK_PREFIX):
            # A session's hook script has its own token, which opens this one route and nothing else
            # (ADR-0040); neither the owner's session nor a paired device's tier applies to it.
            if not secrets.compare_digest(
                request.headers.get(HOOK_TOKEN_HEADER, "").encode(), hook_token.encode()
            ):
                return JSONResponse(status_code=401, content={"detail": "invalid hook token"})
            return await call_next(request)
        if path.startswith("/api/") and path not in _PUBLIC_PATHS:
            try:
                security.check(
                    token=request.headers.get(TOKEN_HEADER),
                    host=request.headers.get("host"),
                    origin=request.headers.get("origin"),
                )
                if relay is not None and relay.guard.is_relay(request.headers.get(TOKEN_HEADER)):
                    # Through the relay: the connector vouches for who it is, and the daemon holds
                    # that device to its tier (ADR-0039).
                    relay.guard.authorize(
                        device_id=request.headers.get(DEVICE_HEADER),
                        method=request.method,
                        path=path,
                    )
            except satay.control.AuthError as exc:
                _LOG.warning("%s %s rejected: %s %s", request.method, path, exc.status, exc.detail)
                return JSONResponse(status_code=exc.status, content={"detail": exc.detail})
        response = await call_next(request)
        if login is not None and path.startswith("/api/") and path != "/api/login":
            fresh = login.refresh(request.headers.get(TOKEN_HEADER))
            if fresh is not None:
                response.headers[TOKEN_REFRESH_HEADER] = fresh
        return response

    @app.exception_handler(StarletteHTTPException)
    async def _log_http_error(request: Request, exc: StarletteHTTPException) -> Response:
        # The dashboard only shows a generic line for a failed start; the real reason is
        # in `detail`, and until now nothing wrote it down (ADR-0029). A 404 is routine
        # (a missing asset, an unknown project) and stays at DEBUG.
        level = (
            logging.ERROR
            if exc.status_code >= 500
            else logging.DEBUG
            if exc.status_code == 404
            else logging.WARNING
        )
        _LOG.log(
            level,
            "%s %s -> %s: %s",
            request.method,
            request.url.path,
            exc.status_code,
            exc.detail,
            extra={"project": request.path_params.get("project_id", "")},
        )
        return await http_exception_handler(request, exc)

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
        expose_headers=[TOKEN_REFRESH_HEADER],
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

    def _relay() -> RelayLink:
        if relay is None:
            raise HTTPException(404, "this daemon was not started with --relay")
        return relay

    @app.get("/api/relay")
    async def relay_info(request: Request) -> dict[str, Any]:
        if relay is None:
            return {"enabled": False}
        you = (
            relay.guard.device_for(request.headers)
            if relay.guard.is_relay(request.headers.get(TOKEN_HEADER))
            else None
        )
        return {
            "enabled": True,
            "url": relay.settings.browser_url,
            "page_url": relay.settings.page_url,
            "room": relay.room,
            "you": you.as_json() if you is not None else None,
        }

    @app.get("/api/devices")
    async def list_devices() -> dict[str, Any]:
        link = _relay()
        open_pairing = link.pairing.current()
        return {
            "devices": [d.as_json() for d in link.store.list()],
            "pending": [p.as_json() for p in link.pairing.pending()],
            "pairing": None if open_pairing is None else {"expires_at": open_pairing.expires_at},
        }

    @app.post("/api/devices/pairing")
    async def start_pairing() -> dict[str, Any]:
        payload, expires_at = _relay().start_pairing()
        return {"payload": payload, "expires_at": expires_at}

    @app.delete("/api/devices/pairing")
    async def cancel_pairing() -> dict[str, str]:
        _relay().pairing.cancel()
        return {"status": "cancelled"}

    def _tier_from(body: dict[str, Any], *, allowed: tuple[Tier, ...]) -> Tier | None:
        raw = body.get("tier")
        if raw is None:
            return None
        try:
            tier = parse_tier(str(raw))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if tier not in allowed:
            raise HTTPException(
                400,
                f"a device can only be confirmed as {allowed[0].label} or "
                f"{allowed[-1].label}; raise it afterwards",
            )
        return tier

    @app.post("/api/devices/pending/{pending_id}/confirm")
    async def confirm_device(pending_id: str, request: Request) -> dict[str, Any]:
        link = _relay()
        body = await _json_body(request)
        tier = _tier_from(body, allowed=(Tier.VIEW, Tier.APPROVE)) or DEFAULT_TIER
        try:
            device = link.pairing.confirm(pending_id, tier)
        except DeviceError as exc:
            raise HTTPException(409, str(exc)) from exc
        _LOG.info("device %s (%s) paired as %s", device.id, device.name, device.tier.label)
        return device.as_json()

    @app.post("/api/devices/pending/{pending_id}/reject")
    async def reject_device(pending_id: str) -> dict[str, str]:
        try:
            _relay().pairing.reject(pending_id)
        except DeviceError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"status": "rejected"}

    @app.patch("/api/devices/{device_id}")
    async def update_device(device_id: str, request: Request) -> dict[str, Any]:
        link = _relay()
        body = await _json_body(request)
        tier = _tier_from(body, allowed=tuple(Tier))
        if tier is None:
            raise HTTPException(400, "'tier' is required")
        try:
            device = link.store.set_tier(device_id, tier)
        except DeviceError as exc:
            raise HTTPException(404, str(exc)) from exc
        _LOG.info("device %s is now %s", device.id, device.tier.label)
        return device.as_json()

    @app.delete("/api/devices/{device_id}")
    async def revoke_device(device_id: str) -> dict[str, Any]:
        try:
            device = await _relay().revoke(device_id)
        except DeviceError as exc:
            raise HTTPException(404, str(exc)) from exc
        _LOG.info("device %s (%s) revoked", device.id, device.name)
        return device.as_json()

    def _browser() -> FolderBrowser:
        if folders is None:
            raise HTTPException(404, "this daemon was not started with a folder browser")
        return folders

    @app.get("/api/fs")
    async def list_folders(path: str | None = None) -> dict[str, Any]:
        browser = _browser()
        try:
            listing = await asyncio.to_thread(browser.list_folders, path)
        except OutsideBrowseRootsError as exc:
            raise HTTPException(403, str(exc)) from exc
        except NotAFolderError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {
            "path": listing.path,
            "root": listing.root,
            "roots": [str(root) for root in browser.roots],
            "parent": listing.parent,
            "truncated": listing.truncated,
            "folders": [
                {"name": f.name, "path": f.path, "is_git": f.is_git} for f in listing.folders
            ],
        }

    @app.get("/api/fs/inspect")
    async def inspect_folder(path: str) -> dict[str, Any]:
        browser = _browser()
        try:
            found = await asyncio.to_thread(browser.inspect, path)
        except OutsideBrowseRootsError as exc:
            raise HTTPException(403, str(exc)) from exc
        except NotAFolderError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {
            "path": found.path,
            "name": found.name,
            "is_git": found.is_git,
            "branch": found.branch,
            "dirty": found.dirty,
            "last_commit": found.last_commit,
            "languages": list(found.languages),
        }

    @app.get("/api/permissions")
    async def permissions_catalog() -> dict[str, Any]:
        return {
            "modes": [
                {"name": name, "title": title, "summary": summary}
                for name, (title, summary) in MODE_INFO.items()
            ],
            "presets": [
                {
                    "name": name,
                    "title": PRESET_INFO[name][0],
                    "summary": PRESET_INFO[name][1],
                    "commands": [" ".join(command) for command in commands],
                    "default": name in DEFAULT_PRESETS,
                }
                for name, commands in PRESETS.items()
            ],
            "never_allowed": [
                {"label": label, "summary": summary} for label, summary in NEVER_ALLOWED_SUMMARY
            ],
            "backends": [
                {"name": name, "when": when, "summary": summary}
                for name, when, summary in backend_notes(broker=daemon.broker_enabled())
            ],
        }

    @app.get("/api/limits")
    async def limits_catalog() -> dict[str, Any]:
        """The settings a project or a role may override, each with what it reads now when
        nothing is set (the environment variable, else the built-in default)."""
        return {
            "limits": [
                {
                    "key": spec.key,
                    "title": spec.title,
                    "summary": spec.summary,
                    "unit": spec.unit,
                    "minimum": spec.minimum,
                    "maximum": spec.maximum,
                    "default": spec.effective(),
                    "zero_means": spec.zero_means,
                }
                for spec in LIMIT_SPECS
            ]
        }

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
        try:
            check_root(root)
            check_root_free(root, daemon.projects.list())
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
                presets=_presets_from(body.get("presets")),
            )
        except (ProjectRootError, ProjectRootInUseError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return _project_json(daemon, project.id)

    @app.get("/api/projects/{project_id}")
    async def get_project(project_id: str) -> dict[str, Any]:
        try:
            return _project_json(daemon, project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/projects/{project_id}/environment")
    async def get_environment(project_id: str) -> dict[str, Any]:
        """What the project's files say it needs (ADR-0029, V5-E2): read-only, nothing is run.
        Off the event loop: the root may sit on a slow mount (WSL's /mnt/c)."""
        try:
            project = daemon.projects.get(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        spec = await asyncio.to_thread(environment.detect, project.root)
        found = await asyncio.to_thread(envprep.plan, project.root, spec)
        return {
            **spec.to_json(),
            "prepare": {"setting": project.env_prepare, **found.to_json()},
        }

    @app.patch("/api/projects/{project_id}/environment")
    async def update_environment(project_id: str, request: Request) -> dict[str, Any]:
        """Whether cuttlefish installs this project's dependencies before a team starts:
        `ask` (the default), `auto` or `off` (ADR-0029, V5-E3)."""
        body = await _json_body(request)
        setting = body.get("prepare")
        if setting not in envprep.PREPARE_MODES:
            raise HTTPException(400, f"'prepare' must be one of {', '.join(envprep.PREPARE_MODES)}")
        try:
            daemon.projects.update_env_prepare(project_id, setting)
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

    @app.patch("/api/projects/{project_id}/presets")
    async def update_presets(project_id: str, request: Request) -> dict[str, Any]:
        body = await _json_body(request)
        names = body.get("presets")
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise HTTPException(400, "'presets' must be a list of preset names")
        try:
            chosen = validate_presets(names)
            daemon.projects.update_presets(project_id, chosen)
            return _project_json(daemon, project_id)
        except UnknownPresetError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.patch("/api/projects/{project_id}/limits")
    async def update_limits(project_id: str, request: Request) -> dict[str, Any]:
        """Set (or, with an empty object, clear) the project's own limits. A team already
        running keeps the values it started with."""
        body = await _json_body(request)
        if "limits" not in body:
            raise HTTPException(400, "'limits' is required; send {} to clear them")
        limits = _limits_from(body.get("limits"))
        try:
            daemon.projects.update_limits(project_id, limits)
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

    # -- secrets: names and kinds out, values in and never back (ADR-0006) ----------------------

    async def _secret_value(request: Request) -> str:
        body = await _json_body(request)
        value = body.get("value")
        if not isinstance(value, str):
            raise HTTPException(400, "'value' (a string) is required")
        return value

    @app.get("/api/projects/{project_id}/secrets")
    async def list_project_secrets(project_id: str) -> dict[str, Any]:
        try:
            return daemon.list_secrets(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except SecretsUnavailableError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.put("/api/projects/{project_id}/secrets/{name}")
    async def set_project_secret(project_id: str, name: str, request: Request) -> dict[str, Any]:
        value = await _secret_value(request)
        try:
            daemon.set_secret(project_id, name, value)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except SecretsUnavailableError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"name": name, "scope": "project"}

    @app.delete("/api/projects/{project_id}/secrets/{name}")
    async def delete_project_secret(project_id: str, name: str) -> dict[str, Any]:
        try:
            removed = daemon.delete_secret(project_id, name)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except SecretsUnavailableError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not removed:
            raise HTTPException(404, f"no secret {name!r} for this project")
        return {"name": name, "scope": "project"}

    @app.get("/api/secrets")
    async def list_shared_secrets() -> dict[str, Any]:
        return daemon.list_shared_secrets()

    @app.put("/api/secrets/{name}")
    async def set_shared_secret(name: str, request: Request) -> dict[str, Any]:
        value = await _secret_value(request)
        try:
            daemon.set_secret(None, name, value)
        except SecretsUnavailableError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"name": name, "scope": "shared"}

    @app.delete("/api/secrets/{name}")
    async def delete_shared_secret(name: str) -> dict[str, Any]:
        try:
            removed = daemon.delete_secret(None, name)
        except SecretsUnavailableError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not removed:
            raise HTTPException(404, f"no shared secret {name!r}")
        return {"name": name, "scope": "shared"}

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
        prepare = body.get("prepare")
        if prepare not in (None, "yes", "skip"):
            raise HTTPException(400, '\'prepare\', if given, must be "yes" or "skip"')
        try:
            team_id = await daemon.start(
                project_id, roles, require_approval=require_approval, prepare=prepare
            )
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except UnknownRoleError as exc:
            raise HTTPException(400, str(exc)) from exc
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

    @app.get("/api/projects/{project_id}/artifacts")
    async def list_artifacts(project_id: str) -> dict[str, Any]:
        """The files in the project's ``.cuttlefish/artifacts/`` (CUT-97), newest first."""
        try:
            found = await daemon.list_artifacts(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"artifacts": [a.to_json() for a in found]}

    @app.get("/api/projects/{project_id}/artifacts/file")
    async def read_artifact(project_id: str, path: str) -> Response:
        """One artifact file, as data. The browser renders nothing itself (HTML goes out as text)
        and the headers say so, so a file opened directly still runs nothing."""
        try:
            body, content_type = await daemon.read_artifact(project_id, path)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ArtifactNotFoundError as exc:
            raise HTTPException(404, "no such artifact") from exc
        except ArtifactNotPreviewableError as exc:
            raise HTTPException(415, "this kind of file is listed but not shown") from exc
        except ArtifactTooLargeError as exc:
            raise HTTPException(413, "this file is too large to show") from exc
        return Response(
            body,
            media_type=content_type,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Cache-Control": "no-store",
            },
        )

    @app.post("/api/hooks/claude-code/{project_id}")
    async def claude_hook(project_id: str, request: Request) -> dict[str, Any]:
        """A hook of the project's Claude Code session (ADR-0040). A permission hook is held here
        until a person answers or the window ends; the answer is `{}` for no decision."""
        length = int(request.headers.get("content-length") or 0)
        if length > _HOOK_BODY_LIMIT:
            raise HTTPException(413, "hook payload too large")
        body = await _json_body(request)

        async def gone() -> None:
            while not await request.is_disconnected():
                await asyncio.sleep(1.0)

        try:
            return await daemon.claude_hook(project_id, body, gone)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/projects/{project_id}/agent-session")
    async def agent_session(project_id: str) -> dict[str, Any]:
        try:
            return (await daemon.agent_session(project_id)).to_json()
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/projects/{project_id}/agent-session/start")
    async def start_agent_session(project_id: str, request: Request) -> dict[str, Any]:
        body = await _json_body(request)
        backend = body.get("backend")
        if backend is not None and not isinstance(backend, str):
            raise HTTPException(400, "'backend', if given, must be a string")
        try:
            return (await daemon.start_agent_session(project_id, backend)).to_json()
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (FleetError, ConfigError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/projects/{project_id}/chat")
    async def get_chat(project_id: str, after: int | None = None) -> dict[str, Any]:
        """The project's chat thread (CUT-172), read from the agent's own record. ``after`` is the
        ``cursor`` of the previous answer; leave it out for the first read."""
        if after is not None and after < 0:
            raise HTTPException(400, "'after' must not be negative")
        try:
            return await daemon.chat(project_id, after)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/projects/{project_id}/chat")
    async def send_chat(project_id: str, request: Request) -> dict[str, str]:
        """Say something to the builder (CUT-173): the text is pasted into its session and sent.
        Typed access, like the terminal, so the Terminal tier. 409 says why it was not sent."""
        body = await _json_body(request)
        text = body.get("text")
        if not isinstance(text, str):
            raise HTTPException(400, "'text' is required")
        try:
            return await daemon.send_chat(project_id, text)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except FleetError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/projects/{project_id}/autopilot")
    async def get_autopilot(project_id: str) -> dict[str, Any]:
        """Autopilot's state, the plan checklist and what the run has used (CUT-174)."""
        try:
            return await daemon.autopilot_status(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/projects/{project_id}/autopilot/{action}")
    async def autopilot_action(project_id: str, action: str) -> dict[str, Any]:
        """Approve the plan, pause, resume, stop, or ask for the next step now. Each is a fixed
        action, never free text, so they are Approve tier and not typed access."""
        try:
            return await daemon.autopilot_action(project_id, action)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (FleetError, AutopilotError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/projects/{project_id}/goal")
    async def get_goal(project_id: str) -> dict[str, Any]:
        try:
            return {"goal": await daemon.goal(project_id)}
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/projects/{project_id}/goal")
    async def give_goal(project_id: str, request: Request) -> dict[str, Any]:
        """Give the project's builder a goal (CUT-171): start its session with the goal as the
        first message. 409 when it cannot (already running, or a backend with no first message)."""
        body = await _json_body(request)
        text, backend = body.get("text"), body.get("backend")
        if not isinstance(text, str):
            raise HTTPException(400, "'text' is required")
        if backend is not None and not isinstance(backend, str):
            raise HTTPException(400, "'backend', if given, must be a string")
        limits = _autopilot_limits(body.get("autopilot"))
        try:
            goal, session = await daemon.give_goal(project_id, text, backend, autopilot=limits)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (FleetError, ConfigError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"goal": goal, "session": session.to_json()}

    @app.post("/api/projects/{project_id}/agent-session/stop")
    async def stop_agent_session(project_id: str) -> dict[str, Any]:
        try:
            return (await daemon.stop_agent_session(project_id)).to_json()
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.websocket("/api/projects/{project_id}/terminal")
    async def terminal(websocket: WebSocket, project_id: str) -> None:
        """A terminal onto the project's agent session (CUT-96, ADR-0035).

        A browser cannot set a header on a websocket, and a token in the URL would reach logs, so
        the first message is `{"type": "auth", "token": ...}`, checked with the same guard as every
        other route, with the handshake's own `Host` and `Origin`. Then `{"type": "ready"}`, and
        from there binary frames are the terminal's bytes in both directions, and a text frame
        `{"type": "resize", "cols": N, "rows": N}` resizes it. The auth message may add
        `"kind": "shell"` for the person's own shell in the project folder (CUT-170); the default
        is the agent's session. Both are the same route, so both sit in the Terminal tier."""
        await websocket.accept()
        try:
            first = json.loads(await asyncio.wait_for(websocket.receive_text(), 10))
            token = first.get("token") if isinstance(first, dict) else None
            if not isinstance(first, dict) or first.get("type") != "auth":
                raise ValueError("expected an auth message first")
            try:
                security.check(
                    token=token if isinstance(token, str) else None,
                    host=websocket.headers.get("host"),
                    origin=websocket.headers.get("origin"),
                )
            except TypeError as exc:
                # satay's own check compares tokens as `str` and raises on non-ASCII text, which a
                # websocket message (unlike a header) can carry. It is a wrong token all the same.
                raise satay.control.AuthError(401, "invalid token") from exc
            if relay is not None and relay.guard.is_relay(
                token if isinstance(token, str) else None
            ):
                device = first.get("device")
                relay.guard.authorize(
                    device_id=device if isinstance(device, str) else None,
                    method="WS",
                    path=f"/api/projects/{project_id}/terminal",
                )
        except satay.control.AuthError as exc:
            _LOG.warning("terminal for %s rejected: %s %s", project_id, exc.status, exc.detail)
            if isinstance(exc, TierRefusal):
                await websocket.close(code=4403, reason=str(exc.detail)[:120])
            else:
                await websocket.close(code=4401, reason="not authorised")
            return
        except (ValueError, TimeoutError, WebSocketDisconnect):
            await websocket.close(code=4400, reason="expected an auth message")
            return
        raw_size = first.get("size")
        size: dict[str, Any] = raw_size if isinstance(raw_size, dict) else {}
        raw_kind = first.get("kind")
        kind = raw_kind if isinstance(raw_kind, str) else "agent"
        try:
            attach = await daemon.open_terminal(
                project_id,
                cols=_int(size.get("cols"), 120),
                rows=_int(size.get("rows"), 32),
                kind=kind,
            )
        except ProjectNotFoundError:
            await websocket.close(code=4404, reason="no such project")
            return
        except FleetError as exc:
            await websocket.close(code=4404, reason=str(exc)[:120])
            return
        await websocket.send_text(json.dumps({"type": "ready"}))

        async def to_client() -> None:
            async for chunk in attach.read():
                await websocket.send_bytes(chunk)

        async def from_client() -> None:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message.get("bytes") is not None:
                    await attach.write(message["bytes"])
                elif message.get("text"):
                    try:
                        control = json.loads(message["text"])
                    except ValueError:
                        continue
                    if isinstance(control, dict) and control.get("type") == "resize":
                        attach.resize(_int(control.get("cols"), 120), _int(control.get("rows"), 32))

        pumps = [asyncio.create_task(to_client()), asyncio.create_task(from_client())]
        try:
            await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in pumps:
                task.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            await attach.close()
            # Already closed by the peer, or by uvicorn at shutdown (Ctrl-C with a browser open).
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await websocket.close()

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

    @app.get("/api/requests")
    async def list_requests() -> dict[str, Any]:
        return {"requests": [_pending_json(daemon, p) for p in daemon.requests.pending()]}

    @app.get("/api/projects/{project_id}/requests")
    async def list_project_requests(project_id: str) -> dict[str, Any]:
        try:
            project = daemon.projects.get(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {
            "pending": [_pending_json(daemon, p) for p in daemon.requests.pending(project_id)],
            "resolved": [_resolved_json(project, e) for e in daemon.request_history(project_id)],
        }

    @app.post("/api/projects/{project_id}/requests/{request_id}/answer")
    async def answer_request(project_id: str, request_id: str, request: Request) -> Response:
        body = await _json_body(request)
        answer, rule, text = body.get("answer"), body.get("rule"), body.get("text")
        if not isinstance(answer, str):
            raise HTTPException(400, "'answer' (str) is required")
        if rule is not None and not (
            isinstance(rule, list) and all(isinstance(w, str) for w in rule)
        ):
            raise HTTPException(400, "'rule', if given, must be a list of words")
        if text is not None and not isinstance(text, str):
            raise HTTPException(400, "'text', if given, must be a string")
        try:
            outcome = daemon.answer_request(project_id, request_id, answer, rule=rule, text=text)
            already = False
        except ProjectNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except AlreadyResolvedError as exc:
            outcome = exc.outcome
            if (
                outcome.by != "person"
                or outcome.resolution != _ANSWER_RESOLUTION.get(answer)
                or (answer == "answer" and (text or "").strip() != outcome.text)
            ):
                return JSONResponse(
                    status_code=409,
                    content={
                        "detail": str(exc),
                        "resolution": outcome.resolution,
                        "by": outcome.by,
                    },
                )
            already = True  # the same answer again: done, not an error
        except RequestError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return JSONResponse(
            {
                "request_id": outcome.request_id,
                "resolution": outcome.resolution,
                "by": outcome.by,
                "rule": list(outcome.rule) if outcome.rule else None,
                "already": already,
            }
        )

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


class _FleetServer(uvicorn.Server):
    """uvicorn waits for in-flight requests when it shuts down, and a held hook (ADR-0040) is a
    request that waits for a person. Release those first, so Ctrl-C ends the daemon promptly."""

    def __init__(self, config: uvicorn.Config, on_shutdown: Callable[[], None]) -> None:
        super().__init__(config)
        self._on_shutdown = on_shutdown

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        self._on_shutdown()
        await super().shutdown(sockets)


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
    browse_roots: Sequence[Path] = (),
    token_file: Path | None = None,
    relay: RelaySettings | None = None,
    tailnet_name: str | None = None,
) -> None:
    """Serve `daemon`'s HTTP surface until cancelled (`cuttlefish serve`).

    Loopback `host` (the default): a static token, printed to stdout at start. With
    `token_file` (what `cuttlefish serve` passes, CUT-106) the token is read from that file,
    created with mode 0600 the first time, so a restart keeps it and an open dashboard
    carries on; without one, a fresh token per start, as `cuttlefish run --steerable` does.

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
    if tailnet_name is not None:
        if not satay.control.is_loopback_host(host):
            raise ValueError(
                "behind `tailscale serve` the daemon must stay on loopback, or the page is "
                "reachable without the certificate (ADR-0041)"
            )
        if not password:
            raise ValueError(
                "--tailscale-https needs CUTTLEFISH_SERVE_PASSWORD: the page is reachable from "
                "the whole tailnet (ADR-0041)"
            )
    # Behind `tailscale serve` the port is in a command a person ran, so it is never moved.
    resolved_port = port if tailnet_name is not None else find_free_port(host, port)
    login: SessionAuth | None = None
    security: SecurityCheck
    dashboard_note = "  (serving the dashboard build too)" if dashboard_dir is not None else ""
    if tailnet_name is None and satay.control.is_loopback_host(host):
        satay.control.ensure_loopback_bind(host)
        token = (
            load_or_create_token(token_file)
            if token_file is not None
            else satay.control.generate_token()
        )
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
        origins = {*cors_origins, _self_origin(host, resolved_port)}
        if tailnet_name is not None:
            # `tailscale serve` forwards the tailnet Host and the browser's https Origin (ADR-0013).
            # The session guard ignores Host, so the Origin is the one thing to allow.
            origins.add(f"https://{tailnet_name}")
        login = SessionAuth(password=password, allowed_origins=frozenset(origins))
        security = login
        print(
            f"cuttlefish serve: http://{host}:{resolved_port}  "
            f"(non-loopback -- POST /api/login with CUTTLEFISH_SERVE_PASSWORD, ADR-0011)"
            f"{dashboard_note}",
            flush=True,
        )
    _LOG.info("listening on http://%s:%s%s", host, resolved_port, dashboard_note)
    link = RelayLink.create(relay) if relay is not None else None
    if link is not None:
        security = link.wrap(security)
    app = create_app(
        daemon,
        security=security,
        login=login,
        cors_origins=cors_origins,
        dashboard_dir=dashboard_dir,
        folders=FolderBrowser(browse_roots or [Path.home()]),
        relay=link,
        hook_token=hook_files.hook_token(),
    )
    config = uvicorn.Config(
        app,
        host=host,
        port=resolved_port,
        log_level="warning",
        # A safety net: nothing the daemon serves should outlast a shutdown by long.
        timeout_graceful_shutdown=15,
    )
    server = _FleetServer(config, daemon.release_held_hooks)
    connector_task: asyncio.Task[None] | None = None
    if link is not None:
        connector_task = asyncio.create_task(link.connector(app).run())
        print(
            f"cuttlefish serve: relay {link.settings.url} (end-to-end encrypted). Pair a phone "
            "from the dashboard: Account menu, Devices.",
            flush=True,
        )
    hook_files.write_daemon_info(host, resolved_port)
    try:
        await server.serve()
    finally:
        hook_files.remove_daemon_info()
        if connector_task is not None:
            connector_task.cancel()
            await asyncio.gather(connector_task, return_exceptions=True)
        if link is not None:
            link.close()
