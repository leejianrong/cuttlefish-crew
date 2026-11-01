"""cuttlefish's command-line surface (docs/PLAN.md Affordances, QUESTIONS.md Q9).

``cuttlefish run "<task>"`` submits a task and blocks until it reaches a terminal
state, printing a JSON result and exiting with a code from a small fixed set.
``cuttlefish show <task-id>`` renders one task's full episodic record for a person
to read afterward — both derived from exactly the same journal, never a second
transcript (ADR-0004). ``run --steerable``/``run-team --steerable`` expose a local
control API for the run's lifetime and ``cuttlefish steer <task-id> "<message>"``
delivers to it — redirecting a still-running task at the boundary between
delegation rounds, not mid-flight (ADR-0008). ``run --require-approval``/``run-team
--require-approval`` (KAN-1711) additionally block a round from finalizing at all
until ``cuttlefish approve <task-id>`` (or ``--reject "<comment>"``) decides it —
a formal review gate, not just an optional redirect. ``run --max-tokens``/
``--max-cost-usd`` (and their ``run-team`` equivalents, KAN-1712) force that
identical decision the moment a run's own cumulative usage crosses either
ceiling, an automatic trigger for the same gate rather than a separate one.
``cuttlefish mcp`` (KAN-1764) runs an MCP server (stdio transport) wrapping
an already-running ``cuttlefish serve``'s own HTTP API, for MCP-native hosts
that prefer typed tool calls over shelling out to this CLI directly.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from collections.abc import Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import satay
import satay.control
from dotenv import load_dotenv
from satay.journal.events import TERMINAL_STATUSES

from cuttlefish import doctor, logsetup, onboarding, resume, runtime
from cuttlefish.config import (
    AGENT_BACKEND_ENV,
    ConfigError,
    prepare_run,
    resolve_request_window,
    secrets_db_path,
    validate_backend_name,
)
from cuttlefish.episodic.events import PullRequestOpened
from cuttlefish.episodic.redact import Redactor
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.fleet import (
    DEFAULT_FLEET_PORT,
    FleetDaemon,
    WeakPasswordError,
    hook_files,
    run_daemon,
)
from cuttlefish.fleet.claude_hooks import session_scope
from cuttlefish.fleet.daemon import FleetError
from cuttlefish.fleet.pullrequest import open_pull_request
from cuttlefish.fleet.relay_link import RelaySettings
from cuttlefish.fleet.serve_token import default_serve_token_path
from cuttlefish.handover import DEFAULT_TOKEN_BUDGET
from cuttlefish.limits import LIMIT_SPECS, validate_limits
from cuttlefish.permissions import DEFAULT_MODE, MODES
from cuttlefish.projects.schedules import ScheduleNotFoundError, ScheduleStore
from cuttlefish.projects.store import (
    Project,
    ProjectNotFoundError,
    ProjectRootError,
    ProjectRootInUseError,
    ProjectStore,
    RoleDefinition,
    check_root,
    check_root_free,
)
from cuttlefish.relay.server import DEFAULT_RELAY_PORT
from cuttlefish.roles import BUILTIN_ROLES, UnknownTemplateError, role_definition, template_roles
from cuttlefish.secrets.store import (
    SHARED_SCOPE,
    InvalidSecretsKeyError,
    MissingSecretsKeyError,
    SecretsStore,
    generate_key,
)
from cuttlefish.sessions import SessionManager, Tmux, agent_env, default_socket, worktree
from cuttlefish.sessions.manager import read_goal
from cuttlefish.steering import (
    SteeringDeliveryError,
    read_steering_pointer,
    remove_steering_pointer,
    send_approval_decision,
    send_steering_message,
    write_steering_pointer,
)
from cuttlefish.team import RoleInput, run_team
from cuttlefish.workflow import run_task

# Loaded once, at import time, not inside main(): main() also runs in-process in
# tests (never via subprocess - see tests/e2e/test_cli.py's own docstring), and
# those rely on monkeypatch.delenv clearing a credential for the duration of one
# test. Reloading .env on every main() call would put it right back.
load_dotenv()

#: Exit codes (QUESTIONS.md Q9: "a small fixed set").
EXIT_OK = 0
EXIT_TASK_FAILED = 1
EXIT_CONFIG_ERROR = 2
EXIT_WORKFLOW_ERROR = 3

#: `cuttlefish serve`'s own best-effort default (ADR-0012, KAN-1707) -- relative
#: to CWD, matching how `scripts/demo.sh`/`make demo` already invoke it from the
#: repo root. Missing is not an error here (no `--dashboard-dir` was ever asked
#: for); missing for an *explicit* `--dashboard-dir` is (`run_daemon`'s job).
DEFAULT_DASHBOARD_DIR = Path("frontend/dist")


def _resolve_dashboard_dir(explicit: str | None) -> Path | None:
    if explicit is not None:
        return Path(explicit)
    for candidate in (DEFAULT_DASHBOARD_DIR, installed_dashboard_dir()):
        if (candidate / "index.html").exists():
            return candidate
    return None


def installed_dashboard_dir() -> Path:
    """Where `scripts/install.sh` builds the dashboard (ADR-0045), whatever the CWD is."""
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data_home) / "cuttlefish" / "app" / "frontend" / "dist"


def _resolve_tailscale_host() -> str:
    """This machine's own Tailscale IPv4 address (``tailscale ip -4``, ADR-0013)
    -- `cuttlefish serve --tailscale`'s own bind target, so an operator never
    has to hand-copy an ever-changing ``100.x.y.z`` address. Raises
    `RuntimeError` with an actionable message (not a bare `CalledProcessError`)
    for every way this can fail: no `tailscale` binary, `tailscaled` not
    running, or this machine not logged into a tailnet at all (`tailscale up`).
    """
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5
        )
    except FileNotFoundError:
        raise RuntimeError(
            "--tailscale needs the `tailscale` CLI on PATH -- "
            "install it first (https://tailscale.com/download)"
        ) from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("--tailscale: `tailscale ip -4` timed out") from None
    if result.returncode != 0:
        raise RuntimeError(
            "--tailscale: `tailscale ip -4` failed -- is tailscaled running and "
            f"this machine logged in (`tailscale up`)? stderr: {result.stderr.strip()}"
        )
    host = result.stdout.strip()
    if not host:
        raise RuntimeError("--tailscale: `tailscale ip -4` printed no address")
    return host


#: `tailscale serve --https` ports for the dashboard page and for the relay's `wss` (ADR-0041).
TAILSCALE_PAGE_PORT = 443
TAILSCALE_RELAY_PORT = 8443


def _resolve_tailnet_name() -> str:
    """This machine's MagicDNS name, from the tailnet's `CertDomains` (`tailscale status --json`):
    the name its https certificate is for. Raises `RuntimeError` saying what to do when the
    `tailscale` CLI is missing, tailscaled is not running, or HTTPS is off for the tailnet."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10
        )
    except FileNotFoundError:
        raise RuntimeError(
            "--tailscale-https needs the `tailscale` CLI on PATH (https://tailscale.com/download)"
        ) from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("--tailscale-https: `tailscale status --json` timed out") from None
    if result.returncode != 0:
        raise RuntimeError(
            "--tailscale-https: `tailscale status --json` failed -- is tailscaled running and "
            f"this machine logged in? stderr: {result.stderr.strip()}"
        )
    try:
        domains = json.loads(result.stdout).get("CertDomains") or []
    except ValueError:
        raise RuntimeError("--tailscale-https: `tailscale status --json` was not JSON") from None
    if not domains:
        raise RuntimeError(
            "--tailscale-https: HTTPS is not turned on for this tailnet. Turn it on under DNS, "
            "HTTPS Certificates in the Tailscale admin console (it publishes this machine's name, "
            "and so the tailnet's name, in public certificate logs), then try again"
        )
    return str(domains[0])


def _tailscale_https_notes(name: str, port: int, relay_port: int) -> str:
    """What to run, and why it is not run for you: `tailscale serve` changes this node's serve
    configuration, so cuttlefish prints the commands and leaves them to a person (ADR-0041)."""
    return (
        f"cuttlefish serve: --tailscale-https for {name}. cuttlefish does not run `tailscale "
        "serve` for you (it changes this node's serve configuration). Run:\n"
        f"  tailscale serve --bg --https={TAILSCALE_PAGE_PORT} http://127.0.0.1:{port}\n"
        f"  tailscale serve --bg --https={TAILSCALE_RELAY_PORT} http://127.0.0.1:{relay_port}\n"
        f"  cuttlefish relay --port {relay_port}   (in another terminal; it stays on loopback)\n"
        f"Then open https://{name}/ and pair from Account menu, Devices. Undo with "
        "`tailscale serve reset`."
    )


def _mode_input(mode: str) -> dict[str, str]:
    """``--mode`` as the task input's ``access`` -- omitted for standard, so a default run's
    recorded arguments are the ones it always had (replay-safe)."""
    return {"access": mode} if mode != DEFAULT_MODE else {}


def _parse_allow(values: list[str] | None) -> list[list[str]]:
    """Each ``--allow`` value is one allowed command, shell-quoted (e.g. ``"go
    test"``), split into the argv list kopicode's own declared-allowlist grammar
    expects (KAN-1011, docs/SLICES.md V2 step 3). No flag at all keeps V1's
    original default is empty, which `delegate.presets.resolve_allow` turns into the built-in
    dev presets at delegation time.
    """
    return [shlex.split(value) for value in values] if values else []


def _resolve_root_and_project(args: argparse.Namespace) -> tuple[str, str]:
    root = str(Path(args.root).resolve()) if args.root else str(Path.cwd())
    project = args.project if args.project else Path(root).name
    return root, project


def _root_error(root: str) -> str | None:
    """What is wrong with ``--root``, or ``None``. Checked before anything else so a missing
    folder is not reported as a missing backend binary."""
    try:
        check_root(root)
    except ProjectRootError as exc:
        return str(exc)
    return None


async def _resolve_run_id(args: argparse.Namespace, command: str) -> tuple[str | None, int]:
    """The id this run uses (KAN-1806): the `--resume` id if it names an unfinished
    run, else a fresh one -- warning first when unfinished runs already exist, so a
    plain rerun after a crash is never silently a second, unrelated run.
    Returns `(None, exit_code)` when `--resume` can't be honoured."""
    if args.resume:
        status = await resume.run_status(args.resume)
        if status is None:
            print(f"cuttlefish: no run {args.resume!r} in this directory's .satay", file=sys.stderr)
            return None, EXIT_CONFIG_ERROR
        if status in TERMINAL_STATUSES:
            print(f"cuttlefish: run {args.resume!r} already finished ({status})", file=sys.stderr)
            return None, EXIT_CONFIG_ERROR
        print(
            f"cuttlefish: resuming {args.resume} -- repeat the original command's "
            f"arguments exactly; {resume.RESUME_CAVEAT}",
            file=sys.stderr,
        )
        return args.resume, EXIT_OK
    unfinished = await resume.unfinished_runs()
    if unfinished:
        print(
            f"cuttlefish: warning: {len(unfinished)} unfinished run(s) in this directory "
            f"({', '.join(unfinished)}), crashed or still running elsewhere. "
            f"To continue one, re-run the original command with `--resume ID`; "
            f"this {command} starts a new run.",
            file=sys.stderr,
        )
    return str(uuid.uuid4()), EXIT_OK


async def _run(args: argparse.Namespace) -> int:
    task_id, early_exit = await _resolve_run_id(args, "run")
    if task_id is None:
        return early_exit
    root, project = _resolve_root_and_project(args)
    if (error := _root_error(root)) is not None:
        print(f"cuttlefish: {error}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    secret_names = sorted(set(args.secret or []))

    try:
        prepared = prepare_run(project=project, secret_names=secret_names)
    except ConfigError as exc:
        print(f"cuttlefish: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    runtime.configure(prepared.as_runtime())
    if args.resume:
        resume.mark_resumed(prepared.episodic_store, task_id)
    workflow_input = {
        "task_id": task_id,
        "text": args.task,
        "root": root,
        "token_budget": args.token_budget,
        "allow": _parse_allow(args.allow),
        **_mode_input(args.mode),
        "project": project,
        "secret_names": secret_names,
        "steerable": args.steerable,
        "require_approval": args.require_approval,
        "max_tokens": args.max_tokens,
        "max_cost_usd": args.max_cost_usd,
    }

    # A control API + pointer file is needed for every external channel this
    # task might be reachable over -- `--steerable`'s own optional redirect,
    # `--require-approval`'s mandatory review gate (KAN-1711), or a configured
    # `--max-tokens`/`--max-cost-usd` ceiling (KAN-1712), which forces the
    # identical approval wait the moment it's crossed -- a budget-only run with
    # no control API open would have no way for `cuttlefish approve` to ever
    # reach it once that happened.
    needs_control_api = (
        args.steerable
        or args.require_approval
        or args.max_tokens is not None
        or args.max_cost_usd is not None
    )

    try:
        if needs_control_api:
            async with satay.control.run_app() as app:
                print(
                    json.dumps(
                        {
                            "task_id": task_id,
                            "steering": {"base_url": app.base_url, "token": app.token},
                        }
                    )
                )
                write_steering_pointer(task_id, base_url=app.base_url, token=app.token)
                try:
                    handle = satay.start(run_task, workflow_input, run_id=task_id, store=app.store)
                    result = await handle.result()
                finally:
                    remove_steering_pointer(task_id)
        else:
            async with satay.run_app() as store:
                handle = satay.start(run_task, workflow_input, run_id=task_id, store=store)
                result = await handle.result()
    except satay.WorkflowFailedError as exc:
        print(json.dumps({"task_id": task_id, "status": "error", "error": str(exc)}))
        return EXIT_WORKFLOW_ERROR
    finally:
        prepared.close()

    print(json.dumps({"task_id": task_id, **result}))
    return EXIT_OK if result["status"] == "completed" else EXIT_TASK_FAILED


def _parse_roles(values: list[str] | None) -> list[RoleInput]:
    """Each ``--role`` value is ``NAME:TASK_TEXT``, split on the first ``:`` (ADR-0007).
    Requires at least one; a name declared twice is a config error, not a silent
    overwrite of the first role's own task text.
    """
    if not values:
        raise ConfigError("run-team needs at least one --role NAME:TASK_TEXT")
    roles: list[RoleInput] = []
    seen: set[str] = set()
    for value in values:
        name, sep, text = value.partition(":")
        name, text = name.strip(), text.strip()
        if not sep or not name or not text:
            raise ConfigError(f"--role {value!r} must be NAME:TASK_TEXT")
        if name in seen:
            raise ConfigError(f"--role name {name!r} was declared more than once")
        seen.add(name)
        roles.append({"name": name, "text": text})
    return roles


async def _run_team(args: argparse.Namespace) -> int:
    team_id, early_exit = await _resolve_run_id(args, "run-team")
    if team_id is None:
        return early_exit
    root, project = _resolve_root_and_project(args)
    if (error := _root_error(root)) is not None:
        print(f"cuttlefish: {error}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    secret_names = sorted(set(args.secret or []))

    try:
        roles = _parse_roles(args.role)
        role_backends = _parse_role_backends(args.role_backend, {r["name"] for r in roles})
        role_limits = _parse_limits(args.limit, args.role_limit, {r["name"] for r in roles})
        prepared = prepare_run(
            project=project,
            secret_names=secret_names,
            extra_backends=sorted(set(role_backends.values())),
        )
    except ConfigError as exc:
        print(f"cuttlefish: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    runtime.configure(prepared.as_runtime())
    if args.resume:
        resume.mark_resumed(prepared.episodic_store, team_id)
    allow = _parse_allow(args.allow)
    role_inputs: list[RoleInput] = [
        {"name": role["name"], "text": role["text"], "allow": allow, "secret_names": secret_names}
        for role in roles
    ]
    for role_input in role_inputs:
        if role_input["name"] in role_backends:
            role_input["backend"] = role_backends[role_input["name"]]
        if args.mode != DEFAULT_MODE:
            role_input["access"] = args.mode
        if role_input["name"] in role_limits:
            role_input["limits"] = role_limits[role_input["name"]]
    workflow_input = {
        "team_id": team_id,
        "root": root,
        "project": project,
        "roles": role_inputs,
        "token_budget": args.token_budget,
        "steerable": args.steerable,
        "require_approval": args.require_approval,
        "max_tokens": args.max_tokens,
        "max_cost_usd": args.max_cost_usd,
    }

    # See _run's identical comment -- a budget-only or require_approval-only
    # team still needs the control API/pointer file `cuttlefish approve`
    # reaches it through.
    needs_control_api = (
        args.steerable
        or args.require_approval
        or args.max_tokens is not None
        or args.max_cost_usd is not None
    )

    try:
        if needs_control_api:
            async with satay.control.run_app() as app:
                print(
                    json.dumps(
                        {
                            "team_id": team_id,
                            "steering": {"base_url": app.base_url, "token": app.token},
                        }
                    )
                )
                write_steering_pointer(team_id, base_url=app.base_url, token=app.token)
                try:
                    handle = satay.start(run_team, workflow_input, run_id=team_id, store=app.store)
                    result = await handle.result()
                finally:
                    remove_steering_pointer(team_id)
        else:
            async with satay.run_app() as store:
                handle = satay.start(run_team, workflow_input, run_id=team_id, store=store)
                result = await handle.result()
    except satay.WorkflowFailedError as exc:
        print(json.dumps({"team_id": team_id, "status": "error", "error": str(exc)}))
        return EXIT_WORKFLOW_ERROR
    finally:
        prepared.close()

    print(json.dumps({"team_id": team_id, **result}))
    return EXIT_OK if result["status"] == "completed" else EXIT_TASK_FAILED


def _show(args: argparse.Namespace) -> int:
    episodic_store = EpisodicStore.open(Path.cwd() / ".cuttlefish" / "episodic.db")
    try:
        events = list(episodic_store.read(args.task_id))
    finally:
        episodic_store.close()

    if not events:
        print(f"cuttlefish: no events recorded for task {args.task_id!r}", file=sys.stderr)
        return EXIT_TASK_FAILED

    for event in events:
        print(
            f"{event.seq}. {event.ts.isoformat()} {type(event.payload).__name__}: {event.payload}"
        )
    return EXIT_OK


def _steer(args: argparse.Namespace) -> int:
    """Deliver one steering message to a still-running `--steerable` task or team
    role (ADR-0008) -- a thin HTTP client over the pointer file `run`/`run-team
    --steerable` published when they started.
    """
    pointer = read_steering_pointer(args.task_id)
    if pointer is None:
        print(
            f"cuttlefish: no steerable task {args.task_id!r} is currently running "
            "(it may not be --steerable, or may have already finished)",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR

    base_url, token = pointer
    try:
        send_steering_message(
            base_url=base_url, token=token, task_id=args.task_id, role=args.role, text=args.message
        )
    except SteeringDeliveryError as exc:
        print(f"cuttlefish: {exc}", file=sys.stderr)
        return EXIT_TASK_FAILED

    print(f"cuttlefish: steering message sent to {args.task_id!r}")
    return EXIT_OK


def _approve(args: argparse.Namespace) -> int:
    """Deliver one approve/reject decision to a still-running `--require-approval`
    task or team role (KAN-1711) -- the same pointer-file/HTTP-client shape
    `_steer` already uses, since either flag opens the identical control API.

    ``--reject`` requires its own value be non-empty (a mandatory comment,
    matching Paperclip's own "reject with a mandatory comment" shape) -- an
    empty string is a config error, not silently sent as "no comment".
    """
    # A malformed --reject is a config error regardless of whether task_id is
    # even reachable -- checked first so a typo'd task id never masks it.
    if args.reject is not None and not args.reject.strip():
        print("cuttlefish: --reject needs a non-empty comment", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    pointer = read_steering_pointer(args.task_id)
    if pointer is None:
        print(
            f"cuttlefish: no reachable task {args.task_id!r} is currently running "
            "(it may not be --require-approval/--steerable, or may have already finished)",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR

    approved = args.reject is None
    comment = args.reject.strip() if args.reject is not None else None

    base_url, token = pointer
    try:
        send_approval_decision(
            base_url=base_url,
            token=token,
            task_id=args.task_id,
            role=args.role,
            approved=approved,
            comment=comment,
        )
    except SteeringDeliveryError as exc:
        print(f"cuttlefish: {exc}", file=sys.stderr)
        return EXIT_TASK_FAILED

    verb = "approved" if approved else "rejected"
    print(f"cuttlefish: {verb} {args.task_id!r}")
    return EXIT_OK


def _open_secrets_store_or_exit() -> SecretsStore:
    try:
        return SecretsStore.open(secrets_db_path(Path.cwd()))
    except (MissingSecretsKeyError, InvalidSecretsKeyError) as exc:
        print(f"cuttlefish: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_CONFIG_ERROR) from exc


def _read_secret_value(name: str) -> str:
    """A secret's value, kept off the command line and out of shell history —
    prompted (hidden) on a real terminal, or read as one line from stdin when
    piped (e.g. ``echo "$TOKEN" | cuttlefish secrets set --project foo NAME``).
    """
    if sys.stdin.isatty():
        import getpass

        return getpass.getpass(f"Value for {name}: ")
    return sys.stdin.readline().rstrip("\n")


def _secrets(args: argparse.Namespace) -> int:
    if args.secrets_command == "generate-key":
        print(generate_key())
        return EXIT_OK

    store = _open_secrets_store_or_exit()
    scope = SHARED_SCOPE if args.shared else args.project
    try:
        if args.secrets_command == "set":
            store.set(scope, args.name, _read_secret_value(args.name))
            print(f"cuttlefish: set {args.name!r} for scope {scope!r}")
            return EXIT_OK
        if args.secrets_command == "get":
            value = store.get(scope, args.name)
            if value is None:
                print(f"cuttlefish: no secret {args.name!r} in scope {scope!r}", file=sys.stderr)
                return EXIT_TASK_FAILED
            print(value)
            return EXIT_OK
        if args.secrets_command == "delete":
            if not store.delete(scope, args.name):
                print(f"cuttlefish: no secret {args.name!r} in scope {scope!r}", file=sys.stderr)
                return EXIT_TASK_FAILED
            print(f"cuttlefish: deleted {args.name!r} from scope {scope!r}")
            return EXIT_OK
        # args.secrets_command == "list"
        for name in store.list_names(scope):
            print(name)
        return EXIT_OK
    finally:
        store.close()


def _parse_limit(value: str, flag: str) -> tuple[str, int]:
    """``KEY=N`` as a limit setting (ADR-0030); a bad key or a value out of range is a config error
    saying which flag and why."""
    key, sep, number = value.partition("=")
    if not sep:
        raise ConfigError(f"{flag} {value!r} must be KEY=NUMBER")
    try:
        found = validate_limits({key.strip(): int(number)})
    except ValueError as exc:  # a non-number, or LimitsError (a ValueError) with the reason
        raise ConfigError(f"{flag} {value!r}: {exc}") from exc
    return key.strip(), found[key.strip()]


def _parse_limits(
    values: list[str] | None, role_values: list[str] | None, known_roles: set[str]
) -> dict[str, dict[str, int]]:
    """Each role's own limits: ``--limit KEY=N`` for every role, ``--role-limit NAME:KEY=N`` laid
    over it for one (the dashboard's project and role settings, ADR-0030). Roles with none are
    absent, so a team that sets nothing has the identical input it always had."""
    shared = dict(_parse_limit(v, "--limit") for v in values or [])
    per_role: dict[str, dict[str, int]] = {}
    for value in role_values or []:
        name, sep, rest = value.partition(":")
        if not sep:
            raise ConfigError(f"--role-limit {value!r} must be NAME:KEY=NUMBER")
        if name not in known_roles:
            raise ConfigError(f"--role-limit names {name!r}, which is not a declared role")
        key, number = _parse_limit(rest, "--role-limit")
        per_role.setdefault(name, {})[key] = number
    return {
        name: limits for name in known_roles if (limits := {**shared, **per_role.get(name, {})})
    }


def _parse_role_backends(values: list[str] | None, known_roles: set[str]) -> dict[str, str]:
    """Each ``--role-backend`` value is ``NAME=BACKEND`` (KAN-1809): that role runs
    through BACKEND instead of the default. Names a role not declared, or an
    unknown backend, are config errors rather than silently ignored."""
    result: dict[str, str] = {}
    for value in values or []:
        name, sep, backend = value.partition("=")
        name, backend = name.strip(), backend.strip()
        if not sep or not name or not backend:
            raise ConfigError(f"--role-backend {value!r} must be NAME=BACKEND")
        if name not in known_roles:
            raise ConfigError(f"--role-backend names {name!r}, which is not a declared role")
        result[name] = validate_backend_name(backend, source="--role-backend")
    return result


def _parse_role_definitions(
    values: list[str] | None, role_backends: list[str] | None = None
) -> list[RoleDefinition]:
    """Each ``--role`` value is ``NAME`` or ``NAME:PERSONA`` (ADR-0009) -- unlike
    ``run-team``'s ``--role`` (`_parse_roles`), a persona is optional: a project
    can register a role's name now and give it a voice later
    (``cuttlefish projects update-roles``), or never -- an unregistered persona
    just means a start request for that role runs with no persona prefix.
    """
    if not values:
        return []
    roles: list[RoleDefinition] = []
    seen: set[str] = set()
    for value in values:
        name, _sep, persona = value.partition(":")
        name, persona = name.strip(), persona.strip()
        if not name:
            raise ConfigError(f"--role {value!r} must be NAME or NAME:PERSONA")
        if name in seen:
            raise ConfigError(f"--role name {name!r} was declared more than once")
        seen.add(name)
        # A built-in name with no persona of its own is that built-in (V4-B).
        roles.append(
            role_definition(name)
            if not persona and name in BUILTIN_ROLES
            else RoleDefinition(name=name, persona=persona)
        )
    backends = _parse_role_backends(role_backends, seen)
    return [
        RoleDefinition(
            name=r.name, persona=r.persona, backend=backends.get(r.name), access=r.access
        )
        for r in roles
    ]


def _project_dict(project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "name": project.name,
        "root": project.root,
        "secrets_scope": project.secrets_scope,
        "backend": project.backend,
        "terminal_command": project.terminal_command,
        "mode": project.mode,
        "roles": [
            {
                "name": r.name,
                "persona": r.persona,
                "backend": r.backend,
                "access": r.access,
            }
            for r in project.roles
        ],
        "last_team_id": project.last_team_id,
        "allow": [list(command) for command in project.allow],
        "max_tokens": project.max_tokens,
        "max_cost_usd": project.max_cost_usd,
    }


def _init(args: argparse.Namespace) -> int:
    """``cuttlefish init`` (KAN-1808): see `cuttlefish.onboarding`."""
    env = dict(os.environ)
    backend = args.backend or onboarding.detect_backend(env, shutil.which)
    if backend is None:
        print(
            "cuttlefish: no coding-agent CLI found on PATH. Install one of "
            "kopicode, claude, or codex, then re-run `cuttlefish init`.",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR
    root = Path(args.root)
    checks = onboarding.check_prerequisites(backend, env=env, which=shutil.which, home=Path.home())
    print(f"cuttlefish init -- backend: {backend}")
    for check in checks:
        print(f"  [{'ok' if check.ok else '!!'}] {check.name}: {check.detail}")
    if not all(check.ok for check in checks):
        print("Fix the [!!] items above and re-run `cuttlefish init`.", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        roles = tuple(_parse_role_definitions(args.role)) or onboarding.DEFAULT_ROLES
    except ConfigError as exc:
        print(f"cuttlefish: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    store = ProjectStore.open()
    try:
        project, created = onboarding.register_or_reuse(
            store, name=args.name or root.resolve().name, root=root, roles=roles
        )
    finally:
        store.close()
    verb = "registered" if created else "already registered"
    print(f"  [ok] project {project.name!r} {verb} ({project.root})")
    print(f"  roles: {', '.join(r.name for r in project.roles) or '(none)'}")
    print("\nNext, one task against this repo:\n")
    print("  " + onboarding.next_command(backend, root))
    print("\nOr the dashboard with every registered project:\n\n  make demo")
    return EXIT_OK


def _doctor(args: argparse.Namespace) -> int:
    """Report what is set up and what is quietly wrong. Values are never printed."""
    store = ProjectStore.open()
    try:
        checks = doctor.run_checks(
            environ=os.environ,
            projects=store.list(),
            log_path=logsetup.default_log_path(),
            show_all_env=args.all_env,
        )
    finally:
        store.close()
    for check in checks:
        print(check.render())
    failed = doctor.exit_code(checks)
    print("\ncuttlefish doctor:", "problems found" if failed else "no problems that stop a start")
    return EXIT_TASK_FAILED if failed else EXIT_OK


def _project_pull_request(store: ProjectStore, project_id: str, *, push: bool) -> int:
    """`projects pr`: open a pull request for the project's goal branch with `gh` (CUT-241). The
    body has the goal and the commits; the page's digest needs the daemon and is left out."""
    try:
        project = store.get(project_id)
    except ProjectNotFoundError:
        print(f"cuttlefish: no project {project_id!r} registered", file=sys.stderr)
        return EXIT_TASK_FAILED
    result = open_pull_request(
        project.root,
        read_goal(project.root),
        [],
        push=push,
        scrub=lambda text: Redactor().scrub(text)[0],
    )
    if not result.ok or not result.url or not result.branch or not result.base or not result.title:
        hint = " (rerun with --push to push it)" if result.code == "not_pushed" else ""
        print(f"cuttlefish: {result.reason}{hint}", file=sys.stderr)
        return EXIT_TASK_FAILED
    path = Path(project.root) / ".cuttlefish" / "episodic.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    journal = EpisodicStore.open(path)
    try:
        scope = session_scope(project.id)
        # A repeated call finds the same open pull request: journal it once.
        if not result.existing or not any(
            isinstance(e.payload, PullRequestOpened) and e.payload.url == result.url
            for e in journal.read(scope)
        ):
            journal.append(
                scope,
                PullRequestOpened(
                    result.url, result.branch, result.base, result.title, result.existing
                ),
            )
    finally:
        journal.close()
    if result.warning:
        print(f"cuttlefish: warning: {result.warning}", file=sys.stderr)
    print(result.url)
    return EXIT_OK


def _project_remove_worktree(store: ProjectStore, project_id: str, *, delete_branch: bool) -> int:
    """`projects remove-worktree`: remove the last goal's worktree (CUT-250). Refuses uncommitted
    changes; keeps the branch unless `--delete-branch`."""
    try:
        project = store.get(project_id)
    except ProjectNotFoundError:
        print(f"cuttlefish: no project {project_id!r} registered", file=sys.stderr)
        return EXIT_TASK_FAILED
    goal = read_goal(project.root)
    path = goal.get("worktree") if goal else None
    if not goal or not path:
        print("cuttlefish: the last goal has no worktree", file=sys.stderr)
        return EXIT_TASK_FAILED
    running = (
        SessionManager(Tmux(default_socket()), env_for=agent_env).status(project.id, project.root)
    ).state == "running"
    result = worktree.remove(
        project.root,
        path,
        goal.get("branch"),
        delete_branch=delete_branch,
        builder_running=running,
    )
    if not result.ok:
        print(f"cuttlefish: {result.reason}", file=sys.stderr)
        return EXIT_TASK_FAILED
    print(f"cuttlefish: removed {path}" + (f"; {result.branch_note}" if result.branch_note else ""))
    return EXIT_OK


def _projects(args: argparse.Namespace) -> int:
    store = ProjectStore.open()
    try:
        if args.projects_command == "add":
            try:
                if args.template and args.role:
                    raise ConfigError("--template and --role cannot be combined")
                if args.template:
                    roles = list(template_roles(args.template))
                else:
                    roles = _parse_role_definitions(args.role, args.role_backend) or list(
                        template_roles()
                    )
                backend = (
                    validate_backend_name(args.backend, source="--backend")
                    if args.backend
                    else None
                )
            except (ConfigError, UnknownTemplateError) as exc:
                print(f"cuttlefish: {exc}", file=sys.stderr)
                return EXIT_CONFIG_ERROR
            allow = tuple(tuple(command) for command in _parse_allow(args.allow))
            if (error := _root_error(str(Path(args.root).resolve()))) is not None:
                print(f"cuttlefish: {error}", file=sys.stderr)
                return EXIT_CONFIG_ERROR
            try:
                check_root_free(args.root, store.list())
            except ProjectRootInUseError as exc:
                print(f"cuttlefish: {exc}", file=sys.stderr)
                return EXIT_CONFIG_ERROR
            project = store.register(
                name=args.name,
                root=str(Path(args.root).resolve()),
                secrets_scope=args.secrets_scope,
                roles=tuple(roles),
                allow=allow,
                max_tokens=args.max_tokens,
                max_cost_usd=args.max_cost_usd,
                backend=backend,
                mode=args.mode,
                terminal_command=(args.terminal_command or "").strip() or None,
            )
            print(json.dumps(_project_dict(project)))
            return EXIT_OK
        if args.projects_command == "pr":
            return _project_pull_request(store, args.project_id, push=args.push)
        if args.projects_command == "remove-worktree":
            return _project_remove_worktree(
                store, args.project_id, delete_branch=args.delete_branch
            )
        if args.projects_command == "list":
            for project in store.list():
                print(json.dumps(_project_dict(project)))
            return EXIT_OK
        # args.projects_command == "remove"
        if not store.deregister(args.project_id):
            print(f"cuttlefish: no project {args.project_id!r} registered", file=sys.stderr)
            return EXIT_TASK_FAILED
        print(f"cuttlefish: deregistered {args.project_id!r} (its files were left untouched)")
        return EXIT_OK
    finally:
        store.close()


def _schedule(args: argparse.Namespace) -> int:
    """`schedule add|list|remove|enable|disable` (CUT-246). Prints JSON lines. A running daemon
    reads the table on every pass, so a change here needs no restart."""
    store = ScheduleStore.open()
    try:
        if args.schedule_command == "add":
            projects = ProjectStore.open()
            try:
                projects.get(args.project_id)
            finally:
                projects.close()
            print(
                json.dumps(
                    store.add(
                        args.project_id, args.goal, args.recurrence, now=datetime.now(UTC)
                    ).to_json()
                )
            )
        elif args.schedule_command == "list":
            for entry in store.entries(args.project):
                print(json.dumps(entry.to_json()))
        elif args.schedule_command == "remove":
            if not store.remove(args.schedule_id):
                raise ScheduleNotFoundError(args.schedule_id)
            print(f"cuttlefish: removed schedule {args.schedule_id!r}")
        else:
            on = args.schedule_command == "enable"
            entry = store.set_enabled(args.schedule_id, on, now=datetime.now(UTC))
            print(json.dumps(entry.to_json()))
        return EXIT_OK
    except ScheduleNotFoundError as exc:
        print(f"cuttlefish: no schedule {str(exc)!r}", file=sys.stderr)
    except ProjectNotFoundError:
        print(f"cuttlefish: no project {args.project_id!r} registered", file=sys.stderr)
    except ValueError as exc:  # a recurrence that is not one of the spellings, or an empty goal
        print(f"cuttlefish: {exc}", file=sys.stderr)
    finally:
        store.close()
    return EXIT_TASK_FAILED


async def _serve(args: argparse.Namespace) -> int:
    try:
        request_window = resolve_request_window()
    except ConfigError as exc:
        print(f"cuttlefish serve: {exc}", file=sys.stderr)
        return EXIT_TASK_FAILED
    try:
        store = ProjectStore.open()
    except OSError as exc:
        print(f"cuttlefish serve: cannot open the project registry: {exc}", file=sys.stderr)
        return EXIT_TASK_FAILED
    schedules = ScheduleStore.open()
    daemon = FleetDaemon(store, request_window_s=request_window, schedules=schedules)
    try:
        # ADR-0010/KAN-1703: resume every project whose last team was still
        # running when this process (or a prior `cuttlefish serve`) died, before
        # the HTTP surface opens -- an operator steering a resumed team should
        # never race a `start` request against a resume still in flight.
        for attempt in await daemon.resume_pending():
            if attempt.error is None:
                print(
                    f"cuttlefish serve: resumed {attempt.project_name!r} (team {attempt.team_id})",
                    flush=True,
                )
            else:
                print(
                    f"cuttlefish serve: failed to resume {attempt.project_name!r} "
                    f"(team {attempt.team_id}): {attempt.error}",
                    file=sys.stderr,
                )
        started_id: frozenset[str] = frozenset()
        try:
            manager = daemon.adopt_manager(args.manager)
            if manager is not None and args.manager:
                info = await daemon.start_agent_session(manager.id)
                started_id = frozenset({manager.id})
                print(
                    f"cuttlefish serve: agent session for {manager.name!r} {info.last_action}",
                    flush=True,
                )
        except FleetError as exc:
            print(f"cuttlefish serve: manager not started: {exc}", file=sys.stderr)
        for name, restored in await daemon.reconcile_agent_sessions(started_id):
            if isinstance(restored, str):
                print(
                    f"cuttlefish serve: agent session for {name!r} not restored: {restored}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"cuttlefish serve: agent session for {name!r} {restored.last_action}",
                    flush=True,
                )
        if daemon.scheduler is not None:
            daemon.scheduler.start()
        host = args.host
        tailnet_name: str | None = None
        relay_settings: RelaySettings | None = None
        if args.tailscale_https:
            if args.tailscale:
                print(
                    "cuttlefish serve: --tailscale-https keeps the daemon on loopback, so it "
                    "cannot be combined with --tailscale",
                    file=sys.stderr,
                )
                return EXIT_CONFIG_ERROR
            try:
                tailnet_name = _resolve_tailnet_name()
            except RuntimeError as exc:
                print(f"cuttlefish serve: {exc}", file=sys.stderr)
                return EXIT_TASK_FAILED
        if args.tailscale:
            try:
                host = _resolve_tailscale_host()
            except RuntimeError as exc:
                print(f"cuttlefish serve: {exc}", file=sys.stderr)
                return EXIT_TASK_FAILED
        relay_url = args.relay
        relay_room = args.relay_room
        relay_token = os.environ.get(RELAY_TOKEN_ENV) or None
        if bool(relay_room) != bool(relay_token):
            print(
                f"cuttlefish serve: a hosted relay needs both --relay-room (${RELAY_ROOM_ENV}) "
                f"and ${RELAY_TOKEN_ENV}; make them with `cuttlefish relay-admin create-room`",
                file=sys.stderr,
            )
            return EXIT_CONFIG_ERROR
        if relay_room and not (relay_url or tailnet_name):
            print(
                "cuttlefish serve: --relay-room needs --relay wss://<your relay> to dial",
                file=sys.stderr,
            )
            return EXIT_CONFIG_ERROR
        if tailnet_name is not None:
            relay_url = relay_url or f"ws://127.0.0.1:{DEFAULT_RELAY_PORT}"
            local_relay_port = urlsplit(relay_url).port or DEFAULT_RELAY_PORT
            print(_tailscale_https_notes(tailnet_name, args.port, local_relay_port))
        if relay_url:
            relay_settings = RelaySettings(
                relay_url,
                Path.home() / ".cuttlefish" / "relay-key",
                Path.home() / ".cuttlefish" / "devices.db",
                public_url=(
                    f"wss://{tailnet_name}:{TAILSCALE_RELAY_PORT}" if tailnet_name else None
                ),
                page_url=_relay_page_url(relay_url, tailnet_name, relay_room),
                room=relay_room,
                token=relay_token,
            )
        try:
            await run_daemon(
                daemon,
                host=host,
                port=args.port,
                password=os.environ.get("CUTTLEFISH_SERVE_PASSWORD"),
                cors_origins=args.allow_origin or (),
                dashboard_dir=_resolve_dashboard_dir(args.dashboard_dir),
                browse_roots=[Path(root) for root in args.browse_root or ()],
                token_file=default_serve_token_path(),
                relay=relay_settings,
                tailnet_name=tailnet_name,
            )
        except (ValueError, WeakPasswordError) as exc:
            print(f"cuttlefish serve: {exc}", file=sys.stderr)
            return EXIT_TASK_FAILED
    finally:
        await daemon.close()
        schedules.close()
        store.close()
    return EXIT_OK


#: Read as a fallback default so an MCP host's own launch config (typically
#: env vars, not CLI args -- Claude Desktop/Claude Code's own MCP config
#: shape) can configure this without a wrapper script (KAN-1764).
MCP_BASE_URL_ENV = "CUTTLEFISH_MCP_BASE_URL"
MCP_TOKEN_ENV = "CUTTLEFISH_MCP_TOKEN"


RELAY_DB_ENV = "CUTTLEFISH_RELAY_DB"
RELAY_OWNER_SECRET_ENV = "CUTTLEFISH_RELAY_OWNER_SECRET"
RELAY_ROOM_ENV = "CUTTLEFISH_RELAY_ROOM"
RELAY_TOKEN_ENV = "CUTTLEFISH_RELAY_TOKEN"


def _relay(args: argparse.Namespace) -> int:
    """Run the relay (ADR-0037, ADR-0043). With `--db` it is the hosted, multi-user service: rooms
    belong to accounts and a daemon needs its room's token. Without it the relay is open (no
    accounts, a room is claimed by connecting first), which is only for loopback and a tailnet."""
    from cuttlefish.relay.accounts import AccountError, AccountStore
    from cuttlefish.relay.server import run_relay

    store: AccountStore | None = None
    if args.db:
        secret = os.environ.get(RELAY_OWNER_SECRET_ENV, "")
        try:
            store = AccountStore.open(args.db)
            store.ensure_owner(secret)
        except AccountError as exc:
            print(f"cuttlefish relay: {RELAY_OWNER_SECRET_ENV}: {exc}", file=sys.stderr)
            return EXIT_CONFIG_ERROR
    elif not satay.control.is_loopback_host(args.host):
        print(
            "cuttlefish relay: without --db this relay has no accounts, and anyone who can "
            "reach it could claim a room, so it only runs on loopback. Pass --db to host it.",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR
    pages = Path(args.pages) if args.pages else None
    if pages is not None and not (pages / "index.html").exists():
        print(
            f"cuttlefish relay: {pages} has no index.html (run `make frontend-build`)",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR
    mode = "hosted, accounts on" if store else "open, no accounts"
    print(f"cuttlefish relay: ws://{args.host}:{args.port} ({mode})", flush=True)
    try:
        asyncio.run(
            run_relay(
                args.host,
                args.port,
                store=store,
                pages_dir=pages,
                trust_forwarded=args.trust_forwarded,
                secure_cookies=not args.insecure_cookies,
            )
        )
    except KeyboardInterrupt:
        print("\ncuttlefish relay: stopped", flush=True)
    finally:
        if store is not None:
            store.close()
    return EXIT_OK


def _relay_page_url(relay_url: str, tailnet_name: str | None, relay_room: str | None) -> str | None:
    """Where a phone opens the dashboard: behind `tailscale serve` the daemon's own https name,
    and on a hosted relay the relay itself, which serves the page from the same origin."""
    if tailnet_name:
        return f"https://{tailnet_name}"
    if relay_room:
        parts = urlsplit(relay_url)
        scheme = "https" if parts.scheme == "wss" else "http"
        return f"{scheme}://{parts.netloc}"
    return None


def _relay_admin(args: argparse.Namespace) -> int:
    """Operator commands on a hosted relay's own database (ADR-0043). Run on the machine that
    holds it; there is no admin web page."""
    from cuttlefish.relay.accounts import AccountError, AccountStore

    store = AccountStore.open(args.db)
    try:
        owner = store.owner()
        if owner is None:
            print(
                "cuttlefish relay-admin: no owner yet; start `cuttlefish relay --db` once with "
                f"{RELAY_OWNER_SECRET_ENV} set",
                file=sys.stderr,
            )
            return EXIT_CONFIG_ERROR
        try:
            room, token = store.create_room(owner.id, args.label)
        except AccountError as exc:
            print(f"cuttlefish relay-admin: {exc}", file=sys.stderr)
            return EXIT_CONFIG_ERROR
        print(f"room:  {room.id}")
        print(f"token: {token}")
        print(
            "\nThe token is shown once. On the PC, set "
            f"{RELAY_ROOM_ENV} and {RELAY_TOKEN_ENV} and run `cuttlefish serve --relay wss://<host>`."
        )
    finally:
        store.close()
    return EXIT_OK


def _mcp(args: argparse.Namespace) -> int:
    """Run an MCP server (stdio transport) wrapping an already-running
    `cuttlefish serve`'s own HTTP API (KAN-1764/ADR-0020) -- this process is a
    client of that daemon, not the daemon itself; it never starts one."""
    manager = bool(args.manager)
    # The manager serves its own credential, never the owner's token (ADR-0050).
    token = hook_files.manager_token() if manager else args.token
    base_url = args.base_url
    if manager and not base_url:
        # A restarted daemon may listen elsewhere; its own file says where (ADR-0040, ADR-0052).
        with contextlib.suppress(OSError, ValueError, KeyError):
            base_url = json.loads(hook_files.daemon_info_path().read_text())["url"]
    if not base_url or not token:
        print(
            f"cuttlefish: mcp needs --base-url and --token (or {MCP_BASE_URL_ENV}/{MCP_TOKEN_ENV})",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR

    from cuttlefish.mcp import build_manager_mcp_server, build_mcp_server

    build = build_manager_mcp_server if manager else build_mcp_server
    server = build(base_url=base_url, token=token)
    server.run("stdio")
    return EXIT_OK


def _soak(args: argparse.Namespace) -> int:
    """Score a long unattended Autopilot run (CUT-136), or score a saved one again."""
    import time
    from datetime import UTC, datetime
    from pathlib import Path

    from cuttlefish import soak, soak_run

    if args.soak_command == "score":
        samples, notes = soak.load_run(Path(args.run).read_text())
        if not samples:
            print(f"cuttlefish: {args.run} holds no samples", file=sys.stderr)
            return EXIT_CONFIG_ERROR
        result = soak.score(samples, notes, arm=args.arm, usd_per_mtok=args.usd_per_mtok)
        print(soak.render_report(result, {"title": args.run}))
        return EXIT_OK

    if not args.base_url or not args.token:
        print(
            "cuttlefish: soak run needs --base-url and --token "
            f"(or ${MCP_BASE_URL_ENV}/${MCP_TOKEN_ENV})",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR
    plan = soak_run.RunPlan(
        project_id=args.project,
        goal=args.goal,
        backend=args.backend,
        hours=args.hours,
        tokens=args.tokens,
        nudges=args.nudges,
        arm=args.arm,
        interval_s=args.interval,
        usd_per_mtok=args.usd_per_mtok,
    )
    try:
        soak_run.check_target(args.base_url, allow_default_port=args.allow_default_port)
        print(plan.estimate())
        if not args.yes:
            if not sys.stdin.isatty():
                raise soak_run.SoakError("not a terminal: pass --yes to start without being asked")
            if input("Start this run? Type 'yes' to go ahead: ").strip().lower() != "yes":
                print("Not started.")
                return EXIT_OK
        out_dir = Path(args.out)
        run_file = out_dir / "run.jsonl"
        out_dir.mkdir(parents=True, exist_ok=True)

        def save(text: str) -> None:
            run_file.write_text(text)

        outcome = soak_run.run_soak(
            soak_run.HttpApi(args.base_url, args.token),
            plan,
            clock=time.monotonic,
            sleep=time.sleep,
            now_iso=lambda: datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            save=save,
            log=lambda line: print(f"soak: {line}", flush=True),
        )
    except soak_run.SoakError as exc:
        print(f"cuttlefish: soak: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    for path in soak_run.write_outputs(out_dir, outcome):
        print(f"wrote {path}")
    print()
    print(outcome.report)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cuttlefish")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Submit a task and block until it finishes")
    run_parser.add_argument("task", help="The task text, in plain language")
    run_parser.add_argument(
        "--root",
        default=None,
        help="The repository or scratch checkout to delegate against (default: CWD). A one-shot "
        "run has no Needs-you inbox: a command nothing approves is refused, not asked (use "
        "`serve` to be asked)",
    )
    run_parser.add_argument(
        "--token-budget",
        type=int,
        default=DEFAULT_TOKEN_BUDGET,
        help="Working-memory handover threshold, in estimated tokens",
    )
    run_parser.add_argument(
        "--allow",
        action="append",
        metavar="CMD",
        help=(
            "One shell command the delegation may run inside --root, shell-quoted "
            "(e.g. --allow 'go test'). Repeatable. Added to the built-in dev presets (tests, "
            "linters, builds, git) -- see docs-site/cli-reference.md."
        ),
    )
    run_parser.add_argument(
        "--mode",
        choices=MODES,
        default=DEFAULT_MODE,
        help=(
            "Permission mode (ADR-0025): ask-first (no shell command runs on its own), "
            "standard (the built-in dev presets plus --allow; default) or auto (any "
            "command except the never-allowed list)."
        ),
    )
    run_parser.add_argument(
        "--project",
        default=None,
        help=(
            "This task's secrets scope (ADR-0006). Default: --root's own directory "
            "name. Only matters if CUTTLEFISH_SECRETS_KEY is set."
        ),
    )
    run_parser.add_argument(
        "--secret",
        action="append",
        metavar="NAME",
        help=(
            "One named secret (beyond a backend's own ambient credential names) "
            "this task's backend may read from --project's scope or the shared "
            "scope. Repeatable. Requires CUTTLEFISH_SECRETS_KEY to be set."
        ),
    )
    run_parser.add_argument(
        "--steerable",
        action="store_true",
        help=(
            "Expose a local control API for this run (ADR-0008) and print its "
            'base_url/token, so `cuttlefish steer TASK_ID "message"` can redirect '
            "it at the boundary between delegation rounds. Off by default."
        ),
    )
    run_parser.add_argument(
        "--require-approval",
        action="store_true",
        help=(
            "A round never finalizes on its own (KAN-1711) -- blocks until "
            '`cuttlefish approve TASK_ID`/`--reject "<comment>"` decides it. '
            "Also opens the control API (like --steerable) if not already open. "
            "Off by default."
        ),
    )
    run_parser.add_argument(
        "--resume",
        default=None,
        metavar="ID",
        help=(
            "Resume an unfinished run by its id (KAN-1806, ADR-0010) instead of starting "
            "a new one. Repeat the original command's arguments exactly. A round that "
            "was in flight when the run died starts over. Only finished rounds are kept."
        ),
    )
    run_parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Stop and require a decision (KAN-1712) once this run's own cumulative "
            "token usage reaches N -- the same review gate --require-approval uses. "
            "Also opens the control API if not already open. Unset by default (no ceiling)."
        ),
    )
    run_parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=None,
        metavar="USD",
        help=(
            "Stop and require a decision (KAN-1712) once this run's own cumulative "
            "cost reaches USD -- only ever populated by backends that report a real "
            "dollar figure (Claude Code; kopicode reports none). Unset by default."
        ),
    )

    run_team_parser = subparsers.add_parser(
        "run-team", help="Run several named roles concurrently against one project (ADR-0007)"
    )
    run_team_parser.add_argument(
        "--role",
        action="append",
        metavar="NAME:TASK_TEXT",
        help=(
            "One role: a name and its own task text, separated by the first ':' "
            "(e.g. --role builder:'implement the login form'). Repeatable; at "
            "least one is required."
        ),
    )
    run_team_parser.add_argument(
        "--role-backend",
        dest="role_backend",
        action="append",
        metavar="NAME=BACKEND",
        help=(
            "Run that --role through BACKEND (kopicode, claude-code, codex) instead "
            f"of ${AGENT_BACKEND_ENV} (KAN-1809). Repeatable."
        ),
    )
    run_team_parser.add_argument(
        "--limit",
        action="append",
        metavar="KEY=N",
        help=(
            "A limit for every role (ADR-0030): "
            + ", ".join(spec.key for spec in LIMIT_SPECS)
            + ". What it unsets is read from the CUTTLEFISH_* environment (see the CLI reference). "
            "Repeatable. Only kopicode takes the turn, token, context and time limits."
        ),
    )
    run_team_parser.add_argument(
        "--role-limit",
        dest="role_limit",
        action="append",
        metavar="NAME:KEY=N",
        help="A limit for one --role, over --limit (ADR-0030). Repeatable.",
    )
    run_team_parser.add_argument(
        "--resume",
        default=None,
        metavar="ID",
        help=(
            "Resume an unfinished run by its id (KAN-1806, ADR-0010) instead of starting "
            "a new one. Repeat the original command's arguments exactly. A round that "
            "was in flight when the run died starts over. Only finished rounds are kept."
        ),
    )
    run_team_parser.add_argument(
        "--root",
        default=None,
        help="The repository or scratch checkout every role delegates against (default: CWD)",
    )
    run_team_parser.add_argument(
        "--token-budget",
        type=int,
        default=DEFAULT_TOKEN_BUDGET,
        help="Working-memory handover threshold per role, in estimated tokens",
    )
    run_team_parser.add_argument(
        "--allow",
        action="append",
        metavar="CMD",
        help=(
            "One shell command every role may run inside --root, on top of the built-in dev "
            "presets. Repeatable. Applies to all roles."
        ),
    )
    run_team_parser.add_argument(
        "--mode",
        choices=MODES,
        default=DEFAULT_MODE,
        help=(
            "Permission mode (ADR-0025): ask-first (no shell command runs on its own), "
            "standard (the built-in dev presets plus --allow; default) or auto (any "
            "command except the never-allowed list)."
        ),
    )
    run_team_parser.add_argument(
        "--project",
        default=None,
        help="Every role's shared secrets scope (ADR-0006). Default: --root's own directory name.",
    )
    run_team_parser.add_argument(
        "--secret",
        action="append",
        metavar="NAME",
        help="One named secret every role may read (ADR-0006). Repeatable. Applies to all roles.",
    )
    run_team_parser.add_argument(
        "--steerable",
        action="store_true",
        help=(
            "Expose a local control API for this run (ADR-0008), same as `run "
            "--steerable`, applying to every role. Off by default."
        ),
    )
    run_team_parser.add_argument(
        "--require-approval",
        action="store_true",
        help=(
            "A role's round never finalizes on its own (KAN-1711), applying to "
            "every role -- blocks until `cuttlefish approve TEAM_ID --role NAME`/"
            '`--reject "<comment>"` decides it. Off by default.'
        ),
    )
    run_team_parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Stop and require a decision (KAN-1712) once a role's own cumulative "
            "token usage reaches N, applying to every role independently. Also "
            "opens the control API if not already open. Unset by default (no ceiling)."
        ),
    )
    run_team_parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=None,
        metavar="USD",
        help=(
            "Stop and require a decision (KAN-1712) once a role's own cumulative "
            "cost reaches USD, applying to every role independently. Only ever "
            "populated by backends that report a real dollar figure. Unset by default."
        ),
    )

    show_parser = subparsers.add_parser("show", help="Render one task's full episodic record")
    show_parser.add_argument("task_id", help="The task id (the satay run id it was started with)")

    steer_parser = subparsers.add_parser(
        "steer", help="Redirect a still-running --steerable task or team role (ADR-0008)"
    )
    steer_parser.add_argument("task_id", help="The task id (or team id) to steer")
    steer_parser.add_argument("message", help="The message to send")
    steer_parser.add_argument(
        "--role",
        default=None,
        help="Which team role to steer (required for a run-team task; omit for a plain run task)",
    )

    approve_parser = subparsers.add_parser(
        "approve",
        help="Approve or reject a still-running --require-approval task or team role (KAN-1711)",
    )
    approve_parser.add_argument("task_id", help="The task id (or team id) to decide on")
    approve_parser.add_argument(
        "--role",
        default=None,
        help="Which team role to decide on (required for run-team; omit for a plain run task)",
    )
    approve_parser.add_argument(
        "--reject",
        default=None,
        metavar="COMMENT",
        help=(
            "Reject instead of approve, with this mandatory comment explaining why. "
            "Omit entirely to approve (no comment needed)."
        ),
    )

    secrets_parser = subparsers.add_parser(
        "secrets", help="Manage the project-scoped secrets store"
    )
    secrets_sub = secrets_parser.add_subparsers(dest="secrets_command", required=True)
    secrets_sub.add_parser("generate-key", help="Print a fresh CUTTLEFISH_SECRETS_KEY")

    def _add_scope_and_name(p: argparse.ArgumentParser) -> None:
        scope = p.add_mutually_exclusive_group(required=True)
        scope.add_argument("--project", help="The project scope")
        scope.add_argument("--shared", action="store_true", help="The shared scope")
        p.add_argument("name", help="The secret's name, e.g. HUGGINGFACE_TOKEN")

    _add_scope_and_name(
        secrets_sub.add_parser("set", help="Set a secret's value (prompted, or read from stdin)")
    )
    _add_scope_and_name(secrets_sub.add_parser("get", help="Print a secret's value"))
    _add_scope_and_name(secrets_sub.add_parser("delete", help="Delete a secret"))

    list_parser = secrets_sub.add_parser("list", help="List a scope's secret names (never values)")
    list_scope = list_parser.add_mutually_exclusive_group(required=True)
    list_scope.add_argument("--project", help="The project scope")
    list_scope.add_argument("--shared", action="store_true", help="The shared scope")

    init_parser = subparsers.add_parser(
        "init", help="Guided first-run setup: check prerequisites, register this repo"
    )
    init_parser.add_argument("--root", default=".", help="The repo to register (default: .)")
    init_parser.add_argument("--name", default=None, help="Project name (default: the directory's)")
    init_parser.add_argument(
        "--backend",
        choices=("kopicode", "claude-code", "codex"),
        default=None,
        help=f"Coding agent backend (default: ${AGENT_BACKEND_ENV}, else the first CLI on PATH)",
    )
    init_parser.add_argument(
        "--role",
        action="append",
        metavar="NAME[:PERSONA]",
        help="A role to register (repeatable). Default: builder and reviewer.",
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check backends, credentials (by name only), PATH, the log file and each project",
    )
    doctor_parser.add_argument(
        "--all-env",
        action="store_true",
        help="List every variable name an agent is not given, not just the first few",
    )

    projects_parser = subparsers.add_parser(
        "projects", help="Manage the Project registry (ADR-0009)"
    )
    projects_sub = projects_parser.add_subparsers(dest="projects_command", required=True)

    add_parser = projects_sub.add_parser("add", help="Register a project")
    add_parser.add_argument("--name", required=True, help="Display name")
    add_parser.add_argument(
        "--root", required=True, help="The checkout every role delegates against"
    )
    add_parser.add_argument(
        "--secrets-scope",
        dest="secrets_scope",
        default=None,
        help="This project's SecretsStore scope (ADR-0006). Default: --name.",
    )
    add_parser.add_argument(
        "--template",
        metavar="NAME",
        help=(
            "A built-in team: solo-builder, builder-reviewer or full-crew, each with "
            "ready-made role prompts. Default when no --role is given: builder-reviewer."
        ),
    )
    add_parser.add_argument(
        "--role",
        action="append",
        metavar="NAME[:PERSONA]",
        help=(
            "One role: a name, optionally followed by ':' and a persistent "
            "persona/voice (Q31). A built-in name (builder, reviewer, tester, planner, "
            "docs-writer) with no persona gets its built-in prompt. Repeatable."
        ),
    )
    add_parser.add_argument(
        "--role-backend",
        dest="role_backend",
        action="append",
        metavar="NAME=BACKEND",
        help=(
            "Run this --role through BACKEND (kopicode, claude-code, codex) "
            "instead of the project's default (KAN-1809). Repeatable."
        ),
    )
    add_parser.add_argument(
        "--backend",
        default=None,
        metavar="BACKEND",
        help=(
            "This project's default agent backend (KAN-1809); a role's own "
            f"--role-backend wins. Default: ${AGENT_BACKEND_ENV}."
        ),
    )
    add_parser.add_argument(
        "--terminal-command",
        dest="terminal_command",
        default=None,
        metavar="CMD",
        help=(
            "Run this shell command in the project's tmux agent session instead of Claude Code, "
            "Codex or kopicode (ADR-0047). The Terminal tab attaches to it; there is no live "
            "status, chat, goal or Needs you card for it."
        ),
    )
    add_parser.add_argument(
        "--allow",
        action="append",
        metavar="CMD",
        help=(
            "One shell command every role in this project's team may run "
            "inside --root, shell-quoted (e.g. --allow 'go test'). Repeatable. "
            "Applies to every daemon-started team (`cuttlefish serve`), which has "
            "no CLI --allow flag of its own (Q53). Added to the built-in dev presets."
        ),
    )
    add_parser.add_argument(
        "--mode",
        choices=MODES,
        default=DEFAULT_MODE,
        help=(
            "Permission mode (ADR-0025): ask-first (no shell command runs on its own), "
            "standard (the built-in dev presets plus --allow; default) or auto (any "
            "command except the never-allowed list)."
        ),
    )
    add_parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        metavar="N",
        help=(
            "This project's own run-scoped token ceiling (KAN-1712), applied to "
            "every daemon-started team, which has no CLI flag of its own to carry "
            "it (same reasoning as --allow). Unset by default (no ceiling)."
        ),
    )
    add_parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=None,
        metavar="USD",
        help="This project's own run-scoped cost ceiling (KAN-1712). Unset by default.",
    )

    projects_sub.add_parser("list", help="List every registered project")

    pr_parser = projects_sub.add_parser(
        "pr",
        help="Open a pull request for the project's goal branch with gh (prints its URL)",
    )
    pr_parser.add_argument("project_id")
    pr_parser.add_argument(
        "--push", action="store_true", help="Push the branch to origin first if it is not there"
    )

    rw_parser = projects_sub.add_parser(
        "remove-worktree",
        help="Remove the last goal's git worktree (refused with uncommitted changes)",
    )
    rw_parser.add_argument("project_id")
    rw_parser.add_argument(
        "--delete-branch", action="store_true", help="Also delete its branch, if it is merged"
    )

    schedule_parser = subparsers.add_parser(
        "schedule",
        help="Ask a project's builder for a goal on a recurrence; `cuttlefish serve` starts it",
    )
    schedule_sub = schedule_parser.add_subparsers(dest="schedule_command", required=True)
    sched_add = schedule_sub.add_parser("add", help="Add a schedule")
    sched_add.add_argument("project_id")
    sched_add.add_argument("--goal", required=True, help="What the builder is asked")
    sched_add.add_argument(
        "--recurrence",
        required=True,
        help="'every <N>m', 'every <N>h' or 'daily HH:MM'. Always UTC.",
    )
    sched_list = schedule_sub.add_parser("list", help="List schedules, soonest first")
    sched_list.add_argument("--project", default=None, help="Only this project's")
    for name, text in (
        ("remove", "Delete a schedule"),
        ("enable", "Turn a schedule on (it counts from now)"),
        ("disable", "Turn a schedule off"),
    ):
        schedule_sub.add_parser(name, help=text).add_argument("schedule_id")

    remove_parser = projects_sub.add_parser(
        "remove", help="Deregister a project (never touches its files)"
    )
    remove_parser.add_argument("project_id")

    serve_parser = subparsers.add_parser(
        "serve", help="Start the fleet daemon: launches and owns every registered project's team"
    )
    serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "Loopback by default (ADR-0014). A non-loopback host (a LAN IP, a "
            "Tailscale address, 0.0.0.0) requires CUTTLEFISH_SERVE_PASSWORD in the "
            "environment and switches to password/session auth (ADR-0011) -- never "
            "a CLI flag, so the password never lands in shell history or `ps`."
        ),
    )
    serve_parser.add_argument("--port", type=int, default=DEFAULT_FLEET_PORT)
    serve_parser.add_argument(
        "--tailscale",
        action="store_true",
        help=(
            "Bind directly to this machine's own Tailscale IPv4 address "
            "(`tailscale ip -4`), the recommended remote-access path (ADR-0013) -- "
            "overrides --host. Still needs CUTTLEFISH_SERVE_PASSWORD, since this "
            "is a non-loopback bind; a browser navigating straight to the printed "
            "URL works with no --allow-origin needed. Deliberately not fronted by "
            "`tailscale serve`'s own reverse proxy (ADR-0013: it forwards the "
            "original tailnet Host header verbatim, which a loopback-bound "
            "cuttlefish serve would reject outright)."
        ),
    )
    serve_parser.add_argument(
        "--manager",
        choices=("claude-code", "codex"),
        metavar="BACKEND",
        help=(
            "Run the manager agent (ADR-0052): a project named 'manager' whose session is "
            "started on this backend with the manager's MCP tools, and resumed on every restart "
            "of serve. Once made, serve restores it without this flag."
        ),
    )
    serve_parser.add_argument(
        "--tailscale-https",
        action="store_true",
        help=(
            "Keep the daemon on loopback and print the two `tailscale serve` commands that give "
            "the dashboard an https address and the relay a wss address, so a phone can pair "
            "(ADR-0041). Needs HTTPS turned on for the tailnet (it publishes the machine's name "
            "in public certificate logs) and CUTTLEFISH_SERVE_PASSWORD. Turns the relay on."
        ),
    )
    serve_parser.add_argument(
        "--relay",
        default=os.environ.get("CUTTLEFISH_RELAY_URL"),
        metavar="URL",
        help=(
            "Also dial out to a relay (ws://host:port, `cuttlefish relay`) and print a link that "
            "opens the dashboard through it, end-to-end encrypted (ADR-0037). Loopback only for "
            "now: no accounts, and the link is as strong as the dashboard's password. "
            "Default: $CUTTLEFISH_RELAY_URL."
        ),
    )
    serve_parser.add_argument(
        "--relay-room",
        default=os.environ.get(RELAY_ROOM_ENV),
        metavar="ID",
        help=(
            "The room id a hosted relay gave this PC (`cuttlefish relay-admin create-room`). Its "
            f"registration token is read from ${RELAY_TOKEN_ENV}, never from the command line. "
            f"Default: ${RELAY_ROOM_ENV}."
        ),
    )
    serve_parser.add_argument(
        "--allow-origin",
        action="append",
        metavar="ORIGIN",
        help=(
            "A browser origin (e.g. https://my-machine.ts.net) the dashboard may be "
            "served from when --host is non-loopback (ADR-0011). Repeatable. Ignored "
            "in loopback mode, which already allows same-machine origins. Default: "
            "none -- only same-machine browser access works until this is set."
        ),
    )
    serve_parser.add_argument(
        "--browse-root",
        action="append",
        metavar="PATH",
        help=(
            "A folder the dashboard's folder picker may browse (V4-E, ADR-0026); "
            "subfolders only, hidden ones and links out of the tree excluded. Repeatable. "
            "Default: your home directory. Registering a project is not limited to these."
        ),
    )
    serve_parser.add_argument(
        "--dashboard-dir",
        default=None,
        metavar="PATH",
        help=(
            "Serve the dashboard's own production build (`npm run build` in "
            "frontend/) from this directory, same-origin with the JSON API "
            "(ADR-0012). Default: auto-detect ./frontend/dist relative to the "
            "current directory, silently API-only if it isn't there. An "
            "explicit path with no index.html in it is a startup error, not a "
            "silent fallback."
        ),
    )

    relay_parser = subparsers.add_parser(
        "relay",
        help="Run the relay: a headless server that passes encrypted traffic to a daemon",
    )
    relay_parser.add_argument("--host", default="127.0.0.1")
    relay_parser.add_argument("--port", type=int, default=DEFAULT_RELAY_PORT)
    relay_parser.add_argument(
        "--db",
        default=os.environ.get(RELAY_DB_ENV),
        metavar="PATH",
        help=(
            "Run the hosted, multi-user relay with its accounts in this SQLite file (ADR-0043). "
            f"The owner's sign-in secret comes from ${RELAY_OWNER_SECRET_ENV} (32+ characters). "
            f"Default: ${RELAY_DB_ENV}. Without it the relay is open and loopback-only."
        ),
    )
    relay_parser.add_argument(
        "--pages",
        metavar="DIR",
        help="Also serve this dashboard build (frontend/dist) from the relay's own origin.",
    )
    relay_parser.add_argument(
        "--trust-forwarded",
        action="store_true",
        help=(
            "Count a request against the last X-Forwarded-For address. Only behind a proxy that "
            "sets it, and only when nothing else can reach the relay's port."
        ),
    )
    relay_parser.add_argument(
        "--insecure-cookies",
        action="store_true",
        help="Do not mark the session cookie Secure. For http on loopback only, never hosted.",
    )

    relay_admin_parser = subparsers.add_parser(
        "relay-admin",
        help="Operator commands on a hosted relay's database (create a room and its token)",
    )
    relay_admin_parser.add_argument("action", choices=["create-room"])
    relay_admin_parser.add_argument(
        "--db", default=os.environ.get(RELAY_DB_ENV), required=not os.environ.get(RELAY_DB_ENV)
    )
    relay_admin_parser.add_argument("--label", required=True, help="e.g. 'Work PC'")

    mcp_parser = subparsers.add_parser(
        "mcp",
        help=(
            "Run an MCP server (stdio transport) wrapping an already-running "
            "cuttlefish serve's own HTTP API (KAN-1764)"
        ),
    )
    mcp_parser.add_argument(
        "--base-url",
        default=os.environ.get(MCP_BASE_URL_ENV),
        metavar="URL",
        help=(
            f"The running cuttlefish serve's own base URL, e.g. "
            f"http://127.0.0.1:8420. Default: ${MCP_BASE_URL_ENV}."
        ),
    )
    mcp_parser.add_argument(
        "--token",
        default=os.environ.get(MCP_TOKEN_ENV),
        metavar="TOKEN",
        help=(
            "That daemon's own x-cuttlefish-token -- its printed-once static "
            f"token (loopback default), or a POST /api/login session token "
            f"(non-loopback/password mode, ADR-0011). Default: ${MCP_TOKEN_ENV}."
        ),
    )

    mcp_parser.add_argument(
        "--manager",
        action="store_true",
        help=(
            "Serve the manager agent's tools (attention queue, digest, answer, message, decision) "
            "with the manager credential from ~/.cuttlefish/manager-token, which the daemon holds "
            "to the Approve tier (ADR-0046, ADR-0050). --token is not used."
        ),
    )

    soak_parser = subparsers.add_parser(
        "soak",
        help="Score a long unattended Autopilot run against a scratch daemon (CUT-136)",
    )
    soak_sub = soak_parser.add_subparsers(dest="soak_command", required=True)
    soak_run_parser = soak_sub.add_parser(
        "run",
        help=(
            "Give a project's builder a goal with Autopilot on, watch it, and write a report. "
            "A real run: it asks first, and never answers a permission for the builder"
        ),
    )
    soak_run_parser.add_argument("--base-url", default=os.environ.get(MCP_BASE_URL_ENV))
    soak_run_parser.add_argument("--token", default=os.environ.get(MCP_TOKEN_ENV))
    soak_run_parser.add_argument("--project", required=True, help="Id of a project on that daemon")
    soak_run_parser.add_argument("--goal", required=True, help="What the builder is to do")
    soak_run_parser.add_argument("--backend", default="claude-code")
    soak_run_parser.add_argument("--hours", type=float, default=4.0, help="Wall-clock limit")
    soak_run_parser.add_argument("--tokens", type=int, default=2_000_000, help="Autopilot's limit")
    soak_run_parser.add_argument("--nudges", type=int, default=100, help="Autopilot's limit")
    soak_run_parser.add_argument("--interval", type=float, default=30.0, help="Seconds per sample")
    soak_run_parser.add_argument(
        "--arm", default="baseline", help="A label for the run: baseline (no manager) by default"
    )
    soak_run_parser.add_argument(
        "--usd-per-mtok", type=float, default=None, help="A price, to give a cost per day"
    )
    soak_run_parser.add_argument("--out", required=True, help="Directory for report and run files")
    soak_run_parser.add_argument("--yes", action="store_true", help="Skip the question")
    soak_run_parser.add_argument(
        "--allow-default-port",
        action="store_true",
        help="Allow a daemon on port 8420, which is usually a person's own",
    )
    soak_score_parser = soak_sub.add_parser("score", help="Score a saved run.jsonl again")
    soak_score_parser.add_argument("run", help="A run.jsonl a soak run wrote")
    soak_score_parser.add_argument("--arm", default="baseline")
    soak_score_parser.add_argument("--usd-per-mtok", type=float, default=None)

    return parser


async def _closing_serve_children(coro: Coroutine[Any, Any, int]) -> int:
    """Run `coro`, then end any resident ``kopicode serve`` children it left behind."""
    try:
        return await coro
    finally:
        from cuttlefish.agents.kopicode import close_shared_pool

        await close_shared_pool()


def _run_serve(args: argparse.Namespace) -> int:
    """Run the fleet daemon until Ctrl-C, which is a normal way to stop it, not a crash.

    uvicorn already drains on the first SIGINT, then asyncio.run re-raises it as a
    `KeyboardInterrupt`; unhandled, that prints a traceback. Teams still running are
    resumed by the next `cuttlefish serve` (`resume_pending`), as after any stop.
    """
    log_file = logsetup.configure()
    logging.getLogger(__name__).info(
        "cuttlefish serve starting (pid %s), log file %s", os.getpid(), log_file or "unavailable"
    )
    try:
        return asyncio.run(_closing_serve_children(_serve(args)))
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("cuttlefish serve stopped (Ctrl-C)")
        print("\ncuttlefish serve: stopped", flush=True)
        return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return asyncio.run(_closing_serve_children(_run(args)))
    if args.command == "run-team":
        return asyncio.run(_closing_serve_children(_run_team(args)))
    if args.command == "secrets":
        return _secrets(args)
    if args.command == "steer":
        return _steer(args)
    if args.command == "approve":
        return _approve(args)
    if args.command == "init":
        return _init(args)
    if args.command == "projects":
        return _projects(args)
    if args.command == "schedule":
        return _schedule(args)
    if args.command == "doctor":
        return _doctor(args)
    if args.command == "serve":
        return _run_serve(args)
    if args.command == "relay":
        return _relay(args)
    if args.command == "relay-admin":
        return _relay_admin(args)
    if args.command == "mcp":
        return _mcp(args)
    if args.command == "soak":
        return _soak(args)
    return _show(args)


if __name__ == "__main__":
    sys.exit(main())
