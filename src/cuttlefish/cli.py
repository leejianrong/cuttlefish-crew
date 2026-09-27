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
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import satay
import satay.control
from dotenv import load_dotenv

from cuttlefish import runtime
from cuttlefish.config import ConfigError, prepare_run, secrets_db_path
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.fleet import DEFAULT_FLEET_PORT, FleetDaemon, WeakPasswordError, run_daemon
from cuttlefish.handover import DEFAULT_TOKEN_BUDGET
from cuttlefish.projects.store import Project, ProjectStore, RoleDefinition
from cuttlefish.secrets.store import (
    SHARED_SCOPE,
    InvalidSecretsKeyError,
    MissingSecretsKeyError,
    SecretsStore,
    generate_key,
)
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
    return DEFAULT_DASHBOARD_DIR if (DEFAULT_DASHBOARD_DIR / "index.html").exists() else None


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


def _parse_allow(values: list[str] | None) -> list[list[str]]:
    """Each ``--allow`` value is one allowed command, shell-quoted (e.g. ``"go
    test"``), split into the argv list kopicode's own declared-allowlist grammar
    expects (KAN-1011, docs/SLICES.md V2 step 3). No flag at all keeps V1's
    original default: no shell command allowed.
    """
    return [shlex.split(value) for value in values] if values else []


def _resolve_root_and_project(args: argparse.Namespace) -> tuple[str, str]:
    root = str(Path(args.root).resolve()) if args.root else str(Path.cwd())
    project = args.project if args.project else Path(root).name
    return root, project


async def _run(args: argparse.Namespace) -> int:
    root, project = _resolve_root_and_project(args)
    secret_names = sorted(set(args.secret or []))

    try:
        prepared = prepare_run(project=project, secret_names=secret_names)
    except ConfigError as exc:
        print(f"cuttlefish: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    runtime.configure(prepared.as_runtime())
    task_id = str(uuid.uuid4())
    workflow_input = {
        "task_id": task_id,
        "text": args.task,
        "root": root,
        "token_budget": args.token_budget,
        "allow": _parse_allow(args.allow),
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
    root, project = _resolve_root_and_project(args)
    secret_names = sorted(set(args.secret or []))

    try:
        roles = _parse_roles(args.role)
        prepared = prepare_run(project=project, secret_names=secret_names)
    except ConfigError as exc:
        print(f"cuttlefish: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    runtime.configure(prepared.as_runtime())
    team_id = str(uuid.uuid4())
    allow = _parse_allow(args.allow)
    role_inputs: list[RoleInput] = [
        {"name": role["name"], "text": role["text"], "allow": allow, "secret_names": secret_names}
        for role in roles
    ]
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


def _parse_role_definitions(values: list[str] | None) -> list[RoleDefinition]:
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
        roles.append(RoleDefinition(name=name, persona=persona))
    return roles


def _project_dict(project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "name": project.name,
        "root": project.root,
        "secrets_scope": project.secrets_scope,
        "roles": [{"name": r.name, "persona": r.persona} for r in project.roles],
        "last_team_id": project.last_team_id,
        "allow": [list(command) for command in project.allow],
        "max_tokens": project.max_tokens,
        "max_cost_usd": project.max_cost_usd,
    }


def _projects(args: argparse.Namespace) -> int:
    store = ProjectStore.open()
    try:
        if args.projects_command == "add":
            try:
                roles = _parse_role_definitions(args.role)
            except ConfigError as exc:
                print(f"cuttlefish: {exc}", file=sys.stderr)
                return EXIT_CONFIG_ERROR
            allow = tuple(tuple(command) for command in _parse_allow(args.allow))
            project = store.register(
                name=args.name,
                root=str(Path(args.root).resolve()),
                secrets_scope=args.secrets_scope,
                roles=tuple(roles),
                allow=allow,
                max_tokens=args.max_tokens,
                max_cost_usd=args.max_cost_usd,
            )
            print(json.dumps(_project_dict(project)))
            return EXIT_OK
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


async def _serve(args: argparse.Namespace) -> int:
    store = ProjectStore.open()
    daemon = FleetDaemon(store)
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
        host = args.host
        if args.tailscale:
            try:
                host = _resolve_tailscale_host()
            except RuntimeError as exc:
                print(f"cuttlefish serve: {exc}", file=sys.stderr)
                return EXIT_TASK_FAILED
        try:
            await run_daemon(
                daemon,
                host=host,
                port=args.port,
                password=os.environ.get("CUTTLEFISH_SERVE_PASSWORD"),
                cors_origins=args.allow_origin or (),
                dashboard_dir=_resolve_dashboard_dir(args.dashboard_dir),
            )
        except (ValueError, WeakPasswordError) as exc:
            print(f"cuttlefish serve: {exc}", file=sys.stderr)
            return EXIT_TASK_FAILED
    finally:
        store.close()
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cuttlefish")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Submit a task and block until it finishes")
    run_parser.add_argument("task", help="The task text, in plain language")
    run_parser.add_argument(
        "--root",
        default=None,
        help="The repository or scratch checkout to delegate against (default: CWD)",
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
            "(e.g. --allow 'go test'). Repeatable. Default: no shell command allowed."
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
            "One shell command every role may run inside --root. Repeatable. Applies to all roles."
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
        "--role",
        action="append",
        metavar="NAME[:PERSONA]",
        help=(
            "One role: a name, optionally followed by ':' and a persistent "
            "persona/voice (Q31). Repeatable."
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
            "no CLI --allow flag of its own (Q53). Default: no shell command allowed."
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return asyncio.run(_run(args))
    if args.command == "run-team":
        return asyncio.run(_run_team(args))
    if args.command == "secrets":
        return _secrets(args)
    if args.command == "steer":
        return _steer(args)
    if args.command == "approve":
        return _approve(args)
    if args.command == "projects":
        return _projects(args)
    if args.command == "serve":
        return asyncio.run(_serve(args))
    return _show(args)


if __name__ == "__main__":
    sys.exit(main())
