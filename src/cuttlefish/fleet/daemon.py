"""The fleet daemon (ADR-0009): launches and owns every registered project's team
concurrently, in one process.

One `asyncio.create_task` per project, each its own `satay.control.run_app` pointed
at that project's own `<root>/.satay` -- never a subprocess (`docs/QUESTIONS.md`
Q48/Q50). Status reads go straight to that project's own `.cuttlefish/episodic.db`
(`cuttlefish.fleet.status`), never satay's own live worker state.

`resume_pending` (ADR-0010/KAN-1703) closes ADR-0009's own named daemon-restart
gap: it re-drives every project's last, still-non-terminal team through the
identical `satay.start(run_team, ..., run_id=<the same team_id>)` call satay's
own `RunController.result()` already resumes correctly (proven directly by
`tests/integration/test_crash_recovery.py`, unchanged here) -- cuttlefish was
simply never calling it that way before this fix.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import functools
import logging
import os
import subprocess
import time
import uuid
from collections.abc import Awaitable, Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, TypedDict
from urllib.parse import urlsplit, urlunsplit

import satay
import satay.control
from satay.config import db_path as satay_db_path
from satay.journal.events import TERMINAL_STATUSES
from satay.journal.store import SQLiteStore

from cuttlefish import artifacts, environment, envprep, logsetup, runtime, timeline
from cuttlefish.artifacts import Artifact
from cuttlefish.autopilot import Limits
from cuttlefish.broker import Broker, broker_requested
from cuttlefish.budget import UsageTotals, cumulative_usage
from cuttlefish.config import (
    ConfigError,
    PreparedRun,
    delegate_approvals,
    manager_clear_context,
    manager_daily_budget,
    prepare_run,
    resolve_agent_backend,
)
from cuttlefish.delegate import codex_live
from cuttlefish.delegate.presets import DEFAULT_PRESETS
from cuttlefish.episodic.events import (
    EnvironmentPrepared,
    EnvironmentPrepareStarted,
    EventPayload,
    KillSwitchPressed,
    PullRequestOpened,
    RequestResolved,
    SteeringMessage,
    SteeringQueued,
    StopRequested,
    TaskFailed,
    TeamResumed,
    TeamStopped,
    decode_payload,
)
from cuttlefish.episodic.redact import Redactor
from cuttlefish.episodic.store import EpisodicEvent, EpisodicStore
from cuttlefish.fleet import gitexclude, manager_agent
from cuttlefish.fleet.autopilot import Autopilot
from cuttlefish.fleet.claude_hooks import ClaudeHooks, session_scope
from cuttlefish.fleet.digest import DEFAULT_EVENTS, build_digest
from cuttlefish.fleet.goaldiff import GoalDiff, goal_diff
from cuttlefish.fleet.placement import Placement, RemoteTeam
from cuttlefish.fleet.pullrequest import PullRequest, open_pull_request
from cuttlefish.fleet.scheduler import Scheduler
from cuttlefish.fleet.status import RoleStatus, role_statuses, roles_in
from cuttlefish.limits import merge_limits
from cuttlefish.permissions import DEFAULT_MODE, effective_access
from cuttlefish.projects.schedules import ScheduleStore
from cuttlefish.projects.store import (
    PersistedRole,
    Project,
    ProjectNotFoundError,
    ProjectStore,
    RoleDefinition,
)
from cuttlefish.requests import (
    AlreadyResolvedError,
    HistoryEntry,
    InvalidAnswerError,
    ManagerNotAllowedError,
    Outcome,
    PendingRequest,
    RequestBroker,
    RequestContext,
    RequestError,
    UnknownRequestError,
    granted_rules,
    history,
    unresolved,
)
from cuttlefish.secrets.store import (
    SECRETS_KEY_ENV,
    SHARED_SCOPE,
    InvalidSecretsKeyError,
    SecretsStore,
    check_name,
    check_value,
    default_secrets_db,
)
from cuttlefish.sessions import (
    PtyAttach,
    SessionError,
    SessionInfo,
    SessionManager,
    Tmux,
    agent_env,
    default_socket,
    worktree,
)
from cuttlefish.sessions.chat_input import MessageError, clean_message, dialog_open
from cuttlefish.sessions.codex_record import find_rollout, read_rollout
from cuttlefish.sessions.hooks import write_settings
from cuttlefish.sessions.kopicode_record import find_events, read_events
from cuttlefish.sessions.manager import TERMINAL_BACKEND, read_goal, write_goal
from cuttlefish.sessions.manager import wanted as session_wanted
from cuttlefish.sessions.tmux import TmuxError
from cuttlefish.sessions.transcript import candidate_paths, read_thread
from cuttlefish.sessions.usage import day_usage
from cuttlefish.steering import (
    SteeringDeliveryError,
    cancel_run,
    send_approval_decision,
    send_steering_message,
)
from cuttlefish.team import RoleInput, TeamInput, run_team

logger = logging.getLogger(__name__)

_WAKE_BATCH_S = 2.0
_WAKE_SPACING_S = 30.0
_WAKE_MESSAGE = (
    "Approvals are waiting in project ids: {projects}. Run list_attention, read project_digest for "
    "each, then answer_attention (allow once or deny) what is routine; raise_decision for the rest."
)

#: How long a paste is given to be taken in before Enter is pressed (CUT-173).
_PASTE_SETTLE_S = 0.3
#: How long Claude Code is given to clear its conversation before the next message (CUT-273).
_CLEAR_SETTLE_S = 1.5

#: The longest goal given to a builder (CUT-171): it is one command-line argument.
MAX_GOAL_CHARS = 8000

#: Names the project that hosts the manager agent's own session (ADR-0046); the manager
#: credential may not answer a request or message an agent there.
MANAGER_PROJECT_ENV = "CUTTLEFISH_MANAGER_PROJECT"


class SecretsUnavailableError(Exception):
    """The daemon has no usable secrets key, so no secret can be stored or read."""


class UnknownRoleError(ValueError):
    """A start named a role the project never registered."""


@dataclass(frozen=True, slots=True)
class JournalView:
    """What the project list shows of a project's last team, from one read of its journal."""

    status: dict[str, RoleStatus]
    usage: dict[str, UsageTotals]
    last_event_at: str | None


class FleetError(Exception):
    """A fleet-daemon-level operation failed, distinctly from a workflow's own
    failure (e.g. starting a project that already has a team running)."""


class EnvironmentConfirmationError(FleetError):
    """Dependencies need installing and nobody has said whether to (ADR-0029, V5-E3): the
    project's `env_prepare` is `ask` and the start call named no `prepare`."""

    def __init__(self, project_id: str, plan: envprep.PreparePlan) -> None:
        names = ", ".join(
            f"{s.ecosystem}{'' if s.path == '.' else f' in {s.path}/'} ({s.reason})"
            for s in plan.steps
        )
        super().__init__(
            f"project {project_id!r}: its dependencies need installing first: {names}. "
            "Start again with prepare=yes to install them now, prepare=skip to start without "
            "them, or set the project's environment setting to auto."
        )
        self.plan = plan


def _install_failure_text(ecosystem: str, result: envprep.StepResult, path: str = ".") -> str:
    """Why a role is recorded failed when its project's dependencies would not install: the
    cause in words, and the way out. The output itself is in the install row, not here (the
    last line of a tool's output is often a fragment of a longer sentence)."""
    command = " ".join(result.command)
    cause = {
        "exit": f"{command} exited with code {result.exit_code}",
        "timeout": f"{command} ran too long and was stopped",
        "tool_missing": f"{result.command[0] if result.command else 'the tool'} is not installed",
    }.get(str(result.failure), str(result.failure))
    return (
        f"couldn't install the {ecosystem}{'' if path == '.' else f' ({path}/)'} dependencies "
        f"({cause}). "
        "The install row above has the output; start again with 'Start without installing' "
        "(prepare=skip over the API) to go without."
    )


class UnpushedWorkError(FleetError):
    """The worker the project is on may hold work that is not pushed; say so before moving it."""


_KEEP_HERE = "Keep it on this machine"
_PLACE_ELSEWHERE = "I will place it on another worker"


def _never_reported(worker: str) -> str:
    return (
        f"{worker} never reported this team ending, so changes it made there that were not "
        "pushed to the remote may still be on that machine."
    )


def _handover_text(worker: str, checkout: dict[str, Any] | None) -> str | None:
    """What to warn about before a project leaves `worker`, from the state it reported when its
    team ended: `None` when it said everything is pushed."""
    if not isinstance(checkout, dict):
        return None
    dirty, unpushed = checkout.get("dirty"), checkout.get("unpushed")
    parts = []
    if isinstance(dirty, int) and dirty > 0:
        parts.append(f"{dirty} changed file{'s' if dirty != 1 else ''} not committed")
    if isinstance(unpushed, int) and unpushed > 0:
        parts.append(f"{unpushed} commit{'s' if unpushed != 1 else ''} not pushed")
    if not parts:
        return None
    return f"{worker} holds {' and '.join(parts)}. Push them there first, or the move leaves them."


class RoleStart(TypedDict):
    """One role to start a team with: a name and its own fresh task text.

    Mirrors ``cuttlefish.team.RoleInput`` exactly, kept as a separate type because a
    daemon caller never supplies ``allow``/``secret_names`` (see ``FleetDaemon.start``
    for why) -- this shape says so structurally.
    """

    name: str
    text: str


@dataclass(slots=True)
class RunningTeam:
    """One project's currently in-flight team -- everything `stop`/`steer` need to
    reach it. `status` never reads this -- it reads the episodic journal directly,
    so it keeps working even after the team (or the daemon itself) has stopped."""

    team_id: str
    base_url: str
    token: str
    task: asyncio.Task[None]


def _compose_role_text(role_def: RoleDefinition | None, text: str, env_note: str = "") -> str:
    """A role's task text, with its registered persona prefixed (Q31) -- an
    unregistered role name runs with no persona prefix, a graceful default rather
    than a rejected request (ADR-0009) -- and the environment note (V5-E4, how to run
    things in this project) between the persona and the task."""
    body = f"{env_note}\n\n{text}" if env_note else text
    if role_def is None or not role_def.persona:
        return body
    return f"You are {role_def.name}. {role_def.persona}\n\n{body}"


def _build_role_inputs(
    project: Project, roles: list[RoleStart], env_note: str = ""
) -> list[RoleInput]:
    """`roles`, composed with `project`'s own persistent state -- its registered
    personas (`_compose_role_text`) and its declared `allow` (Q53), applied
    team-wide, the same "one flag, every role" posture the CLI's own
    `run-team --allow` already holds. A project with no declared `allow` gets
    the built-in dev presets (`presets.resolve_allow`, ADR-0023), applied at delegation
    time -- the declaration stored and journaled here stays raw."""
    allow = [list(command) for command in project.allow]
    inputs: list[RoleInput] = []
    for role in roles:
        role_def = project.role(role["name"])
        role_input: RoleInput = {
            "name": role["name"],
            "text": _compose_role_text(role_def, role["text"], env_note),
            "allow": allow,
        }
        if role_def is not None and role_def.backend:
            role_input["backend"] = role_def.backend
        access = effective_access(project.mode, role_def.access if role_def else None)
        if access != DEFAULT_MODE:
            role_input["access"] = access
        if project.presets is not None and list(project.presets) != list(DEFAULT_PRESETS):
            role_input["presets"] = list(project.presets)
        # The project's limits with the role's laid over them; what neither sets is read from the
        # environment when it is used. Composed once, here, and persisted for a resume (ADR-0030).
        limits = merge_limits(project.limits, role_def.limits if role_def else None)
        if limits:
            role_input["limits"] = limits
        inputs.append(role_input)
    return inputs


def _to_persisted_roles(role_inputs: list[RoleInput]) -> tuple[PersistedRole, ...]:
    """`role_inputs` (already persona-composed) as the durable checkpoint
    `ProjectStore.record_team_started` writes -- what `resume_pending` reads
    back verbatim after a restart, so a role's persona is never re-applied a
    second time on resume (`_persisted_roles_to_inputs` does not call
    `_compose_role_text` again)."""
    return tuple(
        PersistedRole(
            name=role["name"],
            text=role["text"],
            allow=tuple(tuple(command) for command in role.get("allow", [])),
            backend=role.get("backend"),
            access=role.get("access"),
            presets=tuple(role["presets"]) if "presets" in role else None,
            limits=role.get("limits") or {},
        )
        for role in role_inputs
    )


def _persisted_roles_to_inputs(roles: tuple[PersistedRole, ...]) -> list[RoleInput]:
    inputs: list[RoleInput] = []
    for role in roles:
        role_input: RoleInput = {
            "name": role.name,
            "text": role.text,
            "allow": [list(command) for command in role.allow],
        }
        if role.backend:
            role_input["backend"] = role.backend
        if role.access:
            role_input["access"] = role.access
        if role.presets is not None:
            role_input["presets"] = list(role.presets)
        if role.limits:
            role_input["limits"] = dict(role.limits)
        inputs.append(role_input)
    return inputs


@dataclass(slots=True, frozen=True)
class ResumeAttempt:
    """One project's own `resume_pending` outcome -- `error` is `None` on success,
    so a caller (`cuttlefish serve`) can print a clear line per project either way
    instead of a resume failure silently leaving that project looking merely
    unstarted."""

    project_id: str
    project_name: str
    team_id: str
    error: str | None


class FleetDaemon:
    """Owns the `Project` registry and every currently-running team."""

    def __init__(
        self,
        project_store: ProjectStore,
        *,
        request_window_s: float = 600.0,
        secrets_db: Path | None = None,
        sessions: SessionManager | None = None,
        schedules: ScheduleStore | None = None,
    ) -> None:
        self._projects = project_store
        #: Each project's agent in tmux (CUT-95); built on first use unless a test supplies one.
        self._sessions = sessions
        #: Where the dashboard's secrets live: ``~/.cuttlefish/secrets.db`` unless a test says.
        self._secrets_db = secrets_db
        self._running: dict[str, RunningTeam] = {}
        #: Teams running on worker machines (ADR-0057); `lookup` is set once workers exist.
        self.placement = Placement()
        # Teams whose operator asked them to stop; the round in flight still has to end.
        self._stopping: set[str] = set()
        #: team id -> its cancel flag, while cuttlefish is installing its dependencies (V5-E3):
        #: such a team has no satay run yet, so a stop cannot go through satay's control API.
        self._preparing: dict[str, asyncio.Event] = {}
        #: How long a person has to answer a request (ADR-0028); the kopicode binary may allow less.
        self._request_window_s = request_window_s
        #: team id -> the episodic store its requests are journaled to, while it runs.
        self._team_stores: dict[str, EpisodicStore] = {}
        self.requests = RequestBroker(self._append_for_team)
        #: The project that hosts the manager agent's own session, if any (ADR-0046): the manager
        #: credential may answer nothing there and message nothing there. CUT-137 sets it.
        self.manager_project: str | None = os.environ.get(MANAGER_PROJECT_ENV) or None
        #: Opt-in (CUT-230, ADR-0053): a permission request wakes the manager agent.
        self.delegate_approvals = delegate_approvals()
        self._wake_task: asyncio.Task[None] | None = None
        self._chat_tasks: set[asyncio.Task[None]] = set()
        self._decision_timers: dict[str, asyncio.Task[None]] = {}
        self._last_wake = 0.0
        self.requests.on_permission = self._wake_manager
        self.requests.on_decision_resolved = self._tell_builder_the_choice
        #: Hooks of a project's Claude Code session raise Needs you cards (ADR-0040).
        self._claude_hooks = ClaudeHooks(self)
        #: project id -> the Codex rollout its chat reads, and when it was last looked for.
        self._codex_records: dict[str, tuple[Path, float]] = {}
        #: Keeps a builder going after its plan is approved (CUT-174, ADR-0044).
        self.autopilot = Autopilot(self)
        #: The credential broker (ADR-0031): started with the first team that needs it.
        self._broker: Broker | None = None
        #: Gives a builder its scheduled goal (CUT-245); only when a schedule store is supplied.
        self.schedules = schedules
        self.scheduler = Scheduler(self, schedules) if schedules is not None else None

    def broker_enabled(self) -> bool:
        return broker_requested(os.environ)

    async def ensure_broker(self) -> Broker | None:
        """The running broker when the setting is on (started now if need be), else None. A broker
        that cannot start is an error, not a quiet fallback to handing out the key."""
        if not self.broker_enabled():
            return None
        if self._broker is None:
            self._broker = Broker()
        await self._broker.start()
        return self._broker

    # -- the agent session in tmux (CUT-95, ADR-0034) ----------------------------------------------

    def _session_manager(self) -> SessionManager:
        if self._sessions is None:
            self._sessions = SessionManager(Tmux(default_socket()), env_for=agent_env)
        return self._sessions

    async def list_artifacts(self, project_id: str) -> list[Artifact]:
        project = self._projects.get(project_id)
        return await asyncio.to_thread(artifacts.list_artifacts, project.root)

    async def read_artifact(self, project_id: str, relative: str) -> tuple[bytes, str]:
        project = self._projects.get(project_id)
        return await asyncio.to_thread(artifacts.read_artifact, project.root, relative)

    async def agent_session(self, project_id: str) -> SessionInfo:
        project = self._projects.get(project_id)
        return await asyncio.to_thread(self._session_manager().status, project.id, project.root)

    async def chat(self, project_id: str, after: int | None = None) -> dict[str, Any]:
        """The project's chat thread, read from the agent's own session record (CUT-172).

        Claude Code (its transcript JSONL) and Codex (its rollout, CUT-177); kopicode reports
        ``supported: false`` with its name, so the page can say where to look instead. ``after``
        is the cursor the last read returned, or ``None`` for the first read."""
        project = self._projects.get(project_id)
        info = await asyncio.to_thread(self._session_manager().status, project.id, project.root)
        base: dict[str, Any] = {
            "backend": info.backend,
            "session": info.state,
            "session_id": info.session_id,
            "supported": info.backend in ("claude-code", "codex", "kopicode"),
            "items": [],
            "cursor": 0,
        }
        if info.backend in ("codex", "kopicode"):
            return await self._file_chat(project, info, base, after)
        if info.backend != "claude-code" or not info.session_id:
            return base
        state, seconds = self._claude_hooks.activity(project.id)
        screen_dialog = False
        if info.state == "running":
            tmux, name = self._session_manager().tmux_session(project.id)
            screen = await asyncio.to_thread(self._screen, tmux, name)
            screen_dialog = dialog_open(screen)
        if screen_dialog:
            state = "dialog"
        elif (
            state == "dialog"
            and info.state == "running"
            and not self._claude_hooks.holds(project.id)
        ):
            # The hooks left a dialog to the terminal, but the screen no longer shows one (it was
            # refused there, which fires no hook): trust the screen.
            state = "unknown"
        base["activity"] = {
            "state": state if info.state == "running" else "none",
            "seconds": seconds,
        }
        hooked = self._claude_hooks.transcript_path(project.id)
        paths = ([Path(hooked)] if hooked else []) + candidate_paths(project.root, info.session_id)
        found = next((p for p in paths if p.is_file()), None)
        if found is None:
            return {**base, "waiting": True}

        def scrub(text: str) -> str:
            return Redactor().scrub(text)[0]

        thread = await asyncio.to_thread(functools.partial(read_thread, found, after, scrub=scrub))
        return {
            **base,
            "items": thread.items,
            "cursor": thread.cursor,
            "earlier": thread.earlier,
            "reset": thread.reset,
            "skipped": thread.skipped,
            "path": str(found),
        }

    def _session_record(self, project: Project, backend: str, started: float) -> Path | None:
        """The record file of a Codex or kopicode session for the project's folder: the one last
        used, unless a newer one has appeared (a new session in the same terminal), looked for at
        most every 15 seconds."""
        cached = self._codex_records.get(project.id)
        now = time.monotonic()
        if cached is not None and cached[0].is_file() and now - cached[1] < 15:
            return cached[0]
        if backend == "codex":
            found = find_rollout(project.root, not_before=started)
        else:
            found = find_events(project.root, not_before=started)
        if found is None:
            return cached[0] if cached is not None and cached[0].is_file() else None
        self._codex_records[project.id] = (found, now)
        return found

    async def _file_chat(
        self, project: Project, info: SessionInfo, base: dict[str, Any], after: int | None
    ) -> dict[str, Any]:
        """The chat of a Codex (CUT-177) or kopicode (CUT-178) builder, read from its own record.
        Neither takes a message from the page yet, so there is no activity to report."""
        started = _parse_stamp(info.at) - 5 if info.at else 0.0
        before = self._codex_records.get(project.id)
        found = await asyncio.to_thread(self._session_record, project, info.backend or "", started)
        if found is None:
            return {**base, "waiting": True}
        reset = before is not None and before[0] != found
        read = read_rollout if info.backend == "codex" else read_events
        thread = await asyncio.to_thread(
            functools.partial(
                read,
                found,
                None if reset else after,
                scrub=lambda t: Redactor().scrub(t)[0],
            )
        )
        return {
            **base,
            "items": thread.items,
            "cursor": thread.cursor,
            "earlier": thread.earlier,
            "reset": thread.reset or reset,
            "skipped": thread.skipped,
            "path": str(found),
        }

    async def overview(self, project_id: str, since: str | None = None) -> dict[str, Any]:
        """The project's goal, Autopilot, timeline and the digest since ``since`` (CUT-176).

        All of it is derived on each call from the journal, the artifacts folder, the repository
        and the builder's transcript; nothing is stored for it (ADR-0004)."""
        project = self._projects.get(project_id)
        # A time the page remembers, or a day back: ISO 8601 UTC strings compare as text.
        since_iso = since or timeline.iso(datetime.now(UTC) - timedelta(days=1))
        journal = await asyncio.to_thread(self.session_events, project)
        artifacts_found = await asyncio.to_thread(artifacts.list_artifacts, project.root)
        env = agent_env(project.root, "shell")  # the agents' filtered environment, no secrets
        branch, commits = await asyncio.to_thread(timeline.read_commits, project.root, env)
        path = await asyncio.to_thread(self.chat_transcript_path, project)
        chat_items: list[dict[str, Any]] = []
        if path is not None:
            chat_items = (await asyncio.to_thread(read_thread, path, None)).items
        events = timeline.build(
            timeline.journal_events(journal),
            timeline.artifact_events(artifacts_found),
            commits,
            timeline.transcript_events(chat_items),
        )
        waiting = len(self.requests.pending(project.id))
        return {
            "since": since_iso,
            "branch": branch,
            "goal": await asyncio.to_thread(read_goal, project.root),
            "autopilot": await self.autopilot.status(project),
            "digest": timeline.digest(events, since_iso, branch, waiting),
            "timeline": [e.to_json() for e in events],
        }

    async def digest(self, project_id: str, last_n: int = DEFAULT_EVENTS) -> dict[str, Any]:
        """The compact, allowlisted digest a manager agent reads (CUT-150)."""
        project = self._projects.get(project_id)
        journal = await asyncio.to_thread(
            lambda: [*self._last_team_events(project), *self._session_events(project)]
        )
        return build_digest(
            project_id=project.id,
            name=project.name,
            events=journal,
            pending=[p.record for p in self.requests.pending(project.id)],
            last_n=last_n,
        )

    async def autopilot_status(self, project_id: str) -> dict[str, Any]:
        return await self.autopilot.status(self._projects.get(project_id))

    async def autopilot_action(self, project_id: str, action: str) -> dict[str, Any]:
        """One of Autopilot's fixed actions, then its state (CUT-174)."""
        project = self._projects.get(project_id)
        actions = {
            "approve": self.autopilot.approve,
            "pause": self.autopilot.pause,
            "resume": self.autopilot.resume,
            "stop": self.autopilot.stop,
            "nudge": self.autopilot.nudge_now,
        }
        if action not in actions:
            raise FleetError(f"no Autopilot action called {action!r}; one of {', '.join(actions)}")
        await actions[action](project)
        return await self.autopilot.status(project)

    @staticmethod
    def _screen(tmux: Tmux, name: str) -> str:
        try:
            return tmux.capture(name)
        except TmuxError:
            return ""

    async def send_chat(self, project_id: str, text: str, *, fresh: bool = False) -> dict[str, str]:
        """Type ``text`` into the project's running Claude Code session as one bracketed paste,
        then Enter (CUT-173). Refused when there is no such session, when the tool is not one
        whose input is wired in yet, and while a dialog is open (a permission waiting, here or in
        the terminal), because a dialog would read the message as its answer. A session that is
        working takes it as a queued message, which the reply says.

        ``fresh`` (the manager's wakes, CUT-273, ADR-0055) types ``/clear`` first when the session
        is idle, so the message starts a short conversation instead of adding to a long one. It
        is Claude Code's own command, no model call; a busy or unknown session is not cleared."""
        try:
            message = clean_message(text)
        except MessageError as exc:
            raise FleetError(str(exc)) from exc
        project = self._projects.get(project_id)
        manager = self._session_manager()
        info = await asyncio.to_thread(manager.status, project.id, project.root)
        if info.state != "running":
            raise FleetError("The builder is not running. Give it a goal on the Overview first.")
        if info.backend != "claude-code":
            raise FleetError(
                "Typing from the chat is only wired in for Claude Code so far; "
                "use the Terminal tab for this builder."
            )
        tmux, name = manager.tmux_session(project.id)
        state, _ = self._claude_hooks.activity(project.id)
        if self._claude_hooks.holds(project.id) or dialog_open(
            await asyncio.to_thread(self._screen, tmux, name)
        ):
            raise FleetError(
                "The builder is waiting on a permission. Answer it first (here or in the "
                "Terminal tab); a message typed now would be read as the answer."
            )

        clear = fresh and state == "idle"

        def deliver() -> None:
            if clear:
                tmux.paste(name, "/clear")
                time.sleep(_PASTE_SETTLE_S)
                tmux.press_enter(name)
                time.sleep(_CLEAR_SETTLE_S)
            tmux.paste(name, message)
            # A pasted block is taken in before the next key is read, or Enter lands in it.
            time.sleep(_PASTE_SETTLE_S)
            tmux.press_enter(name)

        try:
            await asyncio.to_thread(deliver)
        except TmuxError as exc:
            raise FleetError(str(exc)) from exc
        sent = {"sent": "queued" if state == "busy" else "delivered", "state": state}
        return {**sent, "context": "fresh"} if clear else sent

    async def check_in_manager(self, text: str) -> str | None:
        """Type a scheduled check-in into the manager's session (CUT-257, ADR-0053). ``None`` once
        it is typed, else why it was not: a busy session is never interrupted, and a missing
        session, a dialog or a tool without a send path (Codex) refuses in ``send_chat``."""
        assert self.manager_project is not None
        if (over := self._manager_over_budget()) is not None:
            return over
        if self._claude_hooks.activity(self.manager_project)[0] == "busy":
            return "the manager is busy"
        try:
            await self.send_chat(self.manager_project, text, fresh=manager_clear_context())
        except FleetError as exc:
            return str(exc)
        return None

    async def goal(self, project_id: str) -> dict[str, str] | None:
        """The goal last given to the project's builder, or ``None`` (CUT-171). ``pr_url`` is the
        pull request opened for it since, read back from the journal (CUT-241)."""
        project = self._projects.get(project_id)
        goal = await asyncio.to_thread(read_goal, project.root)
        if goal is None:
            return None
        events = await asyncio.to_thread(self.session_events, project)
        opened = [
            e.payload.url
            for e in events
            if isinstance(e.payload, PullRequestOpened) and timeline.iso(e.ts) >= goal.get("at", "")
        ]
        if goal.get("worktree") and not Path(goal["worktree"]).is_dir():
            goal = {k: v for k, v in goal.items() if k != "worktree"}  # removed since (CUT-250)
        return {**goal, "pr_url": opened[-1]} if opened else goal

    async def remove_goal_worktree(
        self, project_id: str, *, delete_branch: bool = False
    ) -> worktree.Removal:
        """Remove the last goal's worktree (CUT-250, ADR-0048). A refusal is a result with a
        reason, as for a pull request. A running builder is refused: it may be working in it."""
        project = self._projects.get(project_id)
        goal = await asyncio.to_thread(read_goal, project.root)
        path = goal.get("worktree") if goal else None
        if not goal or not path:
            return worktree.Removal(False, "not_a_worktree", "The last goal has no worktree.")
        running = (await self.agent_session(project_id)).state == "running"
        return await asyncio.to_thread(
            functools.partial(
                worktree.remove,
                project.root,
                path,
                goal.get("branch"),
                delete_branch=delete_branch,
                builder_running=running,
            )
        )

    async def goal_diff(self, project_id: str) -> GoalDiff:
        """The last goal's branch diff, read from git now (CUT-289). A project placed on a worker
        has its checkout there, so it says so rather than failing."""
        project = self._projects.get(project_id)
        if project.worker_id is not None:
            state = self.worker_state(project.worker_id)
            where = state[0] if state else "a worker machine"
            return GoalDiff(
                False,
                code="on_worker",
                reason=f"{project.name} runs on {where}; its code is there, not on this machine, "
                "so its diff is not shown here yet.",
            )
        goal = await self.goal(project_id)
        return await asyncio.to_thread(goal_diff, project.root, goal)

    async def open_pull_request(self, project_id: str, *, push: bool = False) -> PullRequest:
        """Open a pull request for the goal's branch with the machine's ``gh`` login (CUT-241,
        ADR-0049) and journal it. Refusals come back as a result with a reason, not an error."""
        project = self._projects.get(project_id)
        goal = await asyncio.to_thread(read_goal, project.root)
        overview = await self.overview(project_id, goal.get("at") if goal else None)
        summary = [line for line in overview["digest"] if "waiting for you" not in line]
        result = await asyncio.to_thread(
            functools.partial(
                open_pull_request,
                project.root,
                goal,
                summary,
                push=push,
                scrub=lambda text: Redactor().scrub(text)[0],
            )
        )
        already = result.existing and any(
            isinstance(e.payload, PullRequestOpened) and e.payload.url == result.url
            for e in self.session_events(project)
        )
        if (
            result.ok
            and result.url
            and result.branch
            and result.base
            and result.title
            and not already
        ):
            self.append_session_event(
                project,
                PullRequestOpened(
                    result.url, result.branch, result.base, result.title, result.existing
                ),
            )
        return result

    async def give_goal(
        self,
        project_id: str,
        text: str,
        backend: str | None = None,
        *,
        autopilot: Limits | None = None,
    ) -> tuple[dict[str, str], SessionInfo]:
        """Start the project's builder with ``text`` as its first message (CUT-171).

        The goal goes in as the tool's own first-message argument, not typed into its terminal,
        so it cannot land in a dialog. A builder that is already running has a conversation to type
        to instead (`FleetError`), and the goal is only recorded once the session has started.

        ``autopilot`` (its limits) turns Autopilot on (CUT-174): the builder is told to plan first
        and wait for approval, and the daemon keeps it going after that."""
        goal = text.strip()
        if not goal:
            raise FleetError("Say what the builder should do.")
        if len(goal) > MAX_GOAL_CHARS:
            raise FleetError(
                f"A goal is at most {MAX_GOAL_CHARS} characters; put the detail in a file."
            )
        project = self._projects.get(project_id)
        self._refuse_on_worker(project, "a builder goal")
        if project.terminal_command:
            raise FleetError(
                "This project runs a terminal command, which has no goal or chat. "
                "Start it from the Agent session card and type to it in the Terminal tab."
            )
        chosen = resolve_agent_backend(backend or project.backend)
        made: tuple[str, str] | None = None
        if project.worktree_per_goal or project.mode == "auto":  # Auto is unattended (CUT-183)
            made = await self._goal_worktree(project, goal)
        try:
            info = await self.start_agent_session(
                project_id,
                chosen,
                prompt=goal,
                plan_first=autopilot is not None,
                cwd=made[1] if made else None,
            )
        except BaseException:
            if made:
                await asyncio.to_thread(worktree.discard, project.root, *made)
            raise
        record = await asyncio.to_thread(
            functools.partial(
                write_goal,
                project.root,
                goal,
                chosen,
                branch=made[0] if made else None,
                worktree=made[1] if made else None,
            )
        )
        if autopilot is not None:
            await self.autopilot.begin(project, goal, chosen, autopilot)
        return record, info

    async def _goal_worktree(self, project: Project, goal: str) -> tuple[str, str] | None:
        """A fresh worktree and branch for ``goal`` (ADR-0048), or ``None`` when the builder is
        already running, which `start_agent_session` then refuses with its own message."""
        info = await asyncio.to_thread(self._session_manager().status, project.id, project.root)
        if info.state == "running":
            return None
        await asyncio.to_thread(gitexclude.ensure_excluded, project.root)
        try:
            return await asyncio.to_thread(worktree.create, project.root, goal)
        except worktree.WorktreeError as exc:
            raise FleetError(
                f"This project gives each goal its own git worktree, and that failed: {exc}"
            ) from exc

    async def start_agent_session(
        self,
        project_id: str,
        backend: str | None = None,
        *,
        prompt: str | None = None,
        plan_first: bool = False,
        cwd: str | None = None,
    ) -> SessionInfo:
        """Start the project's agent in tmux, or reattach to it, or relaunch it with its resume
        command. ``backend`` defaults to the project's own, else `CUTTLEFISH_AGENT_BACKEND`.
        ``prompt`` is its first message, for a session being launched now. ``cwd`` is the goal's
        worktree (ADR-0048); a relaunch without one goes back to the last goal's, if it is there."""
        project = self._projects.get(project_id)
        self._refuse_on_worker(project, "the builder session")
        if project.terminal_command:
            # A terminal command (ADR-0047): no backend, no hooks, nothing cuttlefish reads back.
            try:
                return await asyncio.to_thread(
                    functools.partial(
                        self._session_manager().ensure,
                        project.id,
                        project.root,
                        TERMINAL_BACKEND,
                        prompt=prompt,
                        command=project.terminal_command,
                    )
                )
            except SessionError as exc:
                raise FleetError(str(exc)) from exc
        chosen = resolve_agent_backend(backend or project.backend)
        extra_args: list[str] = []
        if project.id == self.manager_project:
            # The manager's launch (ADR-0052), on a restart's relaunch as on a first start.
            try:
                extra_args = await asyncio.to_thread(manager_agent.prepare, project.root, chosen)
            except ValueError as exc:
                raise FleetError(str(exc)) from exc
        settings: Path | None = None
        if chosen == "claude-code":
            # Its permission questions come to Needs you through our hooks (ADR-0040).
            settings = await asyncio.to_thread(
                write_settings, project.root, project.id, self._request_window_s
            )
            self.requests.reopen(session_scope(project.id))
        if cwd is None:
            last = await asyncio.to_thread(read_goal, project.root)
            kept = last.get("worktree") if last else None
            cwd = kept if kept and Path(kept).is_dir() else None
        try:
            return await asyncio.to_thread(
                functools.partial(
                    self._session_manager().ensure,
                    project.id,
                    project.root,
                    chosen,
                    mode=project.mode,
                    hooks_settings=settings,
                    prompt=prompt,
                    plan_first=plan_first,
                    cwd=cwd,
                    extra_args=extra_args,
                )
            )
        except SessionError as exc:
            raise FleetError(str(exc)) from exc

    def adopt_manager(self, backend: str | None = None) -> Project | None:
        """Find the manager's project, making it when ``backend`` is given and there is none
        (CUT-137, ADR-0052), and mark it as the one the manager credential may not touch. With
        neither a project nor a backend, there is no manager and nothing changes. A project
        named by ``CUTTLEFISH_MANAGER_PROJECT`` stays the manager's."""
        root = str(manager_agent.manager_root())
        found = next((p for p in self._projects.list() if p.root == root), None)
        if found is None and backend is None:
            return None
        if backend is not None:
            resolve_agent_backend(backend)
            if backend in manager_agent.UNSUPPORTED:
                raise FleetError(manager_agent.UNSUPPORTED[backend])
        if found is None:
            Path(root).mkdir(parents=True, exist_ok=True)
            found = self._projects.register(
                name=manager_agent.MANAGER_NAME, root=root, backend=backend
            )
        elif backend is not None and found.backend != backend:
            found = self._projects.update_backend(found.id, backend)
        self.manager_project = self.manager_project or found.id
        return found

    async def open_terminal(
        self, project_id: str, *, cols: int, rows: int, kind: str = "agent"
    ) -> PtyAttach:
        """A view onto the project's running agent session (CUT-96). Raises `FleetError` when no
        session is running: the view does not start one, so opening it is never a surprise.

        ``kind="shell"`` (CUT-170) is the person's own shell in the project folder instead. It
        needs no agent and is started when first opened, which is what the person asked for."""
        project = self._projects.get(project_id)
        manager = self._session_manager()
        if kind == "shell":
            try:
                name = await asyncio.to_thread(manager.ensure_shell, project.id, project.root)
            except SessionError as exc:
                raise FleetError(str(exc)) from exc
            return PtyAttach(manager.tmux_session(project.id)[0], name, cols=cols, rows=rows)
        if kind != "agent":
            raise FleetError(f"no terminal called {kind!r}; one of agent, shell")
        info = await asyncio.to_thread(manager.status, project.id, project.root)
        if info.state != "running":
            raise FleetError("no agent session is running; start it from the Overview first")
        tmux, name = manager.tmux_session(project.id)
        return PtyAttach(tmux, name, cols=cols, rows=rows)

    async def stop_agent_session(self, project_id: str) -> SessionInfo:
        project = self._projects.get(project_id)
        # Cards the session holds end now, and the hooks waiting on them are told there is no
        # decision (ADR-0040), before the tmux session goes.
        self._claude_hooks.end_session(project.id)
        await self.autopilot.stop(project, "The builder's session was stopped.")
        return await asyncio.to_thread(self._session_manager().stop, project.id, project.root)

    async def reconcile_agent_sessions(
        self, skip: frozenset[str] = frozenset()
    ) -> list[tuple[str, SessionInfo | str]]:
        """At daemon start, bring back every project whose agent was wanted: reattach to one tmux
        kept running, relaunch one that is gone. A failure is reported, never raised, so one
        project cannot hold the daemon back. ``skip`` names projects started a moment ago."""
        results: list[tuple[str, SessionInfo | str]] = []
        for project in self._projects.list():
            if project.id in skip or not await asyncio.to_thread(session_wanted, project.root):
                continue
            try:
                results.append((project.name, await self.start_agent_session(project.id)))
            except Exception as exc:  # one project's failure must not stop the others
                logger.warning("agent session for %s not restored: %s", project.name, exc)
                results.append((project.name, str(exc)))
        return results

    async def close(self) -> None:
        """Release what the daemon holds open: the schedule loop, the broker and every lease."""
        for timer in list(self._decision_timers.values()):
            timer.cancel()
        if self.scheduler is not None:
            await self.scheduler.close()
        if self._broker is not None:
            await self._broker.close()
            self._broker = None
        for scope in [s for s in self._team_stores if s.startswith("session:")]:
            self._team_stores.pop(scope).close()

    # -- secrets (the dashboard's, ADR-0006): names go out, values never do ----------------------

    def secrets_path(self) -> Path:
        return self._secrets_db if self._secrets_db is not None else default_secrets_db()

    def secrets_enabled(self) -> bool:
        return bool(os.environ.get(SECRETS_KEY_ENV))

    @contextlib.contextmanager
    def _secrets(self) -> Iterator[SecretsStore]:
        if not self.secrets_enabled():
            raise SecretsUnavailableError(
                "Secrets are off: the daemon has no CUTTLEFISH_SECRETS_KEY. Make one with "
                "`cuttlefish secrets generate-key`, set it in the environment of "
                "`cuttlefish serve`, and restart it."
            )
        try:
            store = SecretsStore.open(self.secrets_path())
        except InvalidSecretsKeyError as exc:
            raise SecretsUnavailableError(str(exc)) from exc
        try:
            yield store
        finally:
            store.close()

    def project_secret_names(self, project: Project) -> list[str]:
        """Every secret name this project's agents get: its own and the shared ones."""
        if not self.secrets_enabled():
            return []
        with self._secrets() as store:
            return sorted(
                set(store.list_names(project.secrets_scope)) | set(store.list_names(SHARED_SCOPE))
            )

    def list_secrets(self, project_id: str) -> dict[str, object]:
        """The names set for a project and shared with every project, each marked ``credential``
        (a backend runs on it) or ``secret`` (the project's own). Never a value. ``broker`` says
        whether the credential broker (ADR-0031) is on, so a row can say who can see a key."""
        project = self._projects.get(project_id)
        broker = self.broker_enabled()
        if not self.secrets_enabled():
            return {"enabled": False, "broker": broker, "project": [], "shared": []}
        with self._secrets() as store:
            return {
                "enabled": True,
                "broker": broker,
                "project": _secret_rows(store.list_names(project.secrets_scope)),
                "shared": _secret_rows(store.list_names(SHARED_SCOPE)),
            }

    def list_shared_secrets(self) -> dict[str, object]:
        """The secrets shared with every project. Each row also says which projects have their
        own value of that name (``overridden_in``, they keep it) and which would lose the name if
        it were removed (``lost_by``), by project name."""
        broker = self.broker_enabled()
        if not self.secrets_enabled():
            return {"enabled": False, "broker": broker, "shared": []}
        with self._secrets() as store:
            own = {
                project.name: set(store.list_names(project.secrets_scope))
                for project in self._projects.list()
            }
            rows = _secret_rows(store.list_names(SHARED_SCOPE))
        return {
            "enabled": True,
            "broker": broker,
            "shared": [
                {
                    **row,
                    "overridden_in": sorted(
                        name for name, names in own.items() if row["name"] in names
                    ),
                    "lost_by": sorted(
                        name for name, names in own.items() if row["name"] not in names
                    ),
                }
                for row in rows
            ],
        }

    def set_secret(self, project_id: str | None, name: str, value: str) -> None:
        """Store `value` under `name` for `project_id`, or for every project when it is ``None``.
        Raises ``ValueError`` for a bad name or value, ``SecretsUnavailableError`` without a key."""
        check_name(name)
        check_value(value)
        scope = SHARED_SCOPE if project_id is None else self._projects.get(project_id).secrets_scope
        with self._secrets() as store:
            store.set(scope, name, value)

    def delete_secret(self, project_id: str | None, name: str) -> bool:
        scope = SHARED_SCOPE if project_id is None else self._projects.get(project_id).secrets_scope
        with self._secrets() as store:
            return store.delete(scope, name)

    def _append_for_team(self, team_id: str, payload: EventPayload) -> EpisodicEvent:
        return self._team_stores[team_id].append(team_id, payload)

    @property
    def request_window_s(self) -> float:
        return self._request_window_s

    def release_held_hooks(self) -> None:
        """Called as the HTTP server begins to shut down: see `ClaudeHooks.release_all`."""
        self._claude_hooks.release_all()

    def open_session_journal(self, project: Project) -> None:
        """Make the project's agent session a place requests can be journaled: its own scope in the
        project's episodic store, held open until the daemon closes (ADR-0040)."""
        scope = session_scope(project.id)
        if scope in self._team_stores:
            return
        path = Path(project.root) / ".cuttlefish" / "episodic.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._team_stores[scope] = EpisodicStore.open(path)

    async def claude_hook(
        self,
        project_id: str,
        payload: Mapping[str, Any],
        gone: Callable[[], Awaitable[None]],
    ) -> dict[str, Any]:
        """One hook of the project's Claude Code session (ADR-0040): what to tell its script."""
        return await self._claude_hooks.handle(self._projects.get(project_id), payload, gone)

    def session_events(self, project: Project) -> list[EpisodicEvent]:
        """Every event of the project's agent session, read-only (Autopilot reads its state)."""
        return self._session_events(project)

    def append_session_event(self, project: Project, payload: EventPayload) -> None:
        """Journal ``payload`` under the project's agent session, in the loop thread."""
        self.open_session_journal(project)
        scope = session_scope(project.id)
        self._team_stores[scope].append(scope, payload)

    def chat_transcript_path(self, project: Project) -> Path | None:
        """Where the builder's Claude Code transcript is, or ``None`` (no session or not Claude)."""
        record = self._session_manager().status(project.id, project.root)
        if record.backend != "claude-code" or not record.session_id:
            return None
        hooked = self._claude_hooks.transcript_path(project.id)
        paths = ([Path(hooked)] if hooked else []) + candidate_paths(
            project.root, record.session_id
        )
        return next((p for p in paths if p.is_file()), None)

    def _session_events(self, project: Project) -> list[EpisodicEvent]:
        """Every event of the project's agent session (its hook cards), read-only."""
        path = Path(project.root) / ".cuttlefish" / "episodic.db"
        if not path.exists():
            return []
        store = EpisodicStore.open(path)
        try:
            return list(store.read(session_scope(project.id)))
        finally:
            store.close()

    def answer_request(
        self,
        project_id: str,
        request_id: str,
        answer: str,
        *,
        rule: Sequence[str] | None = None,
        text: str | None = None,
        by: Literal["person", "manager"] = "person",
        tier: str | None = None,
    ) -> Outcome:
        """Answer a pending request of `project_id` (ADR-0028). Raises the broker's
        `RequestError`s. An "Always allow" is applied to the running team by the broker and
        saved to the project's own commands here, for the starts after this one. The manager
        (ADR-0046) may only allow once or deny, never in its own session's project, and never a
        decision (that is the person's)."""
        project = self._projects.get(project_id)
        if by == "manager":
            self.check_manager_may_touch(project_id)
            if answer not in ("allow_once", "allow_always", "deny", "answer"):
                raise InvalidAnswerError("answer must be allow_once or deny")
            pending = next((p for p in self.requests.pending() if p.id == request_id), None)
            if pending is None:
                if answer not in ("allow_once", "deny"):
                    raise self._from_journal(project, request_id)  # 404, or already answered
            elif pending.record.kind == "decision":
                raise ManagerNotAllowedError(
                    "a decision is the person's to answer, not the manager's"
                )
            elif answer not in ("allow_once", "deny"):
                raise ManagerNotAllowedError(
                    "the manager may only allow once or deny; an always-allow rule is the person's"
                )
        try:
            outcome = self.requests.answer(
                request_id, answer, rule=rule, text=text, project_id=project_id, by=by, tier=tier
            )
        except UnknownRequestError:
            # Not in memory: a request from before a restart, or from a finished team. The
            # journal still says how it ended.
            raise self._from_journal(project, request_id) from None
        if outcome.rule is not None:
            self._projects.add_allow(project_id, outcome.rule)
        return outcome

    def _wake_manager(self, request: PendingRequest) -> None:
        """Tell the manager's session a permission request waits (CUT-230, ADR-0053). Coalesced and
        spaced out so a burst is one message; the request stays in Needs you either way, so a
        manager that is down or cannot decide leaves it to the person."""
        if (
            self.manager_project is None
            or request.project_id == self.manager_project
            or not self.delegates_approvals(request.project_id)
            or (self._wake_task is not None and not self._wake_task.done())
        ):
            return
        self._wake_task = asyncio.ensure_future(self._send_wake())

    def manager_budget(self) -> dict[str, Any]:
        """The manager's use today against its daily ceiling (CUT-151). ``metered`` is false when
        its usage cannot be read (only Claude Code writes a transcript); there is never a dollar
        figure, because no backend reports one for a resident session."""
        assert self.manager_project is not None
        project = self._projects.get(self.manager_project)
        try:
            tokens, turns = manager_daily_budget()
            error = None
        except ConfigError as exc:
            tokens = turns = None
            error = str(exc)
        metered = self._session_manager().status(project.id, project.root).backend == "claude-code"
        used = day_usage(project.root, datetime.now().date()) if metered else None
        over = (
            metered
            and used is not None
            and (
                (tokens is not None and used.tokens >= tokens)
                or (turns is not None and used.turns >= turns)
            )
        )
        return {
            "metered": metered,
            "tokens_used": used.tokens if used else None,
            "turns_used": used.turns if used else None,
            "max_tokens": tokens,
            "max_turns": turns,
            "paused": bool(over or error),
            "error": error,
        }

    def _manager_over_budget(self) -> str | None:
        """Why the manager must not be woken now, else ``None``. A pause raises one Needs you card
        and ends it when the manager is under its ceiling again (a new day, or a raised limit).
        Only the daemon's own wakes and check-ins stop; a person can still type to it."""
        assert self.manager_project is not None
        try:
            project = self._projects.get(self.manager_project)
        except ProjectNotFoundError:
            return None
        budget = self.manager_budget()
        scope = session_scope(project.id)
        pending = [
            p for p in self.requests.pending()
            if p.team_id == scope and p.record.kind == "blocked" and p.record.role == "manager"
        ]  # fmt: skip
        if not budget["paused"]:
            if pending:
                self.requests.supersede(scope, "manager")
            return None
        reason = budget["error"] or (
            f"the manager used {budget['tokens_used']:,} tokens and {budget['turns_used']:,} turns "
            "today, which is its daily ceiling"
        )
        if not pending:
            self.open_session_journal(project)
            self.requests.raise_blocked(
                project_id=project.id,
                team_id=scope,
                role="manager",
                backend=None,
                title="The manager is paused: it reached its daily budget",
                detail=f"Wakes and scheduled check-ins are held. {reason[0].upper()}{reason[1:]}.",
                why="Raise CUTTLEFISH_MANAGER_DAILY_TOKENS or CUTTLEFISH_MANAGER_DAILY_TURNS and "
                "restart the daemon, or wait for tomorrow. Messages you type still reach it.",
            )
        return f"paused: {reason}"

    def delegates_approvals(self, project_id: str) -> bool:
        """Whether the manager is woken for ``project_id``'s requests: the project's own choice,
        else the daemon-wide ``CUTTLEFISH_DELEGATE_APPROVALS`` (CUT-259)."""
        try:
            own = self._projects.get(project_id).delegate_approvals
        except ProjectNotFoundError:
            own = None
        return self.delegate_approvals if own is None else own

    async def _send_wake(self) -> None:
        assert self.manager_project is not None
        await asyncio.sleep(
            max(_WAKE_BATCH_S, self._last_wake + _WAKE_SPACING_S - time.monotonic())
        )
        waiting = [
            p
            for p in self.requests.pending()
            if p.record.kind == "permission"
            and p.project_id != self.manager_project
            and self.delegates_approvals(p.project_id)
        ]
        if not waiting or self.manager_project is None or self._manager_over_budget() is not None:
            return
        self._last_wake = time.monotonic()
        projects = ", ".join(sorted({p.project_id for p in waiting}))
        try:
            await self.send_chat(
                self.manager_project,
                _WAKE_MESSAGE.format(projects=projects),
                fresh=manager_clear_context(),
            )
        except FleetError as exc:
            logger.info("manager not woken for approvals: %s", exc)

    def check_manager_may_touch(self, project_id: str) -> None:
        """Refuse the manager credential a project that hosts the manager's own session."""
        if project_id == self.manager_project:
            raise ManagerNotAllowedError("the manager may not act on its own session's project")

    def raise_decision(
        self,
        project_id: str,
        question: str,
        options: Sequence[str],
        recommended: str,
        *,
        role: str = "manager",
        cost_of_waiting: str | None = None,
        wait_s: float | None = None,
        destructive: frozenset[str] = frozenset(),
    ) -> str:
        """Put a decision to the person, in the project's agent-session journal (ADR-0046); the
        builder's (CUT-184) names ``role="builder"``. Returns its request id. Raises the broker's
        `RequestError`s for a bad set of options."""
        project = self._projects.get(project_id)
        self.open_session_journal(project)
        pending = self.requests.raise_decision(
            project_id=project.id,
            team_id=session_scope(project.id),
            question=question,
            options=options,
            recommended=recommended,
            role=role,
            cost_of_waiting=cost_of_waiting,
            wait_s=wait_s,
            destructive=destructive,
        )
        self._arm_decision(pending)
        return pending.id

    def _arm_decision(self, pending: PendingRequest) -> None:
        """Take a decision's recommended option when its wait runs out (CUT-185)."""
        if not pending.record.expires_at:
            return

        async def wait() -> None:
            await asyncio.sleep(max(0.0, (pending.deadline - datetime.now(UTC)).total_seconds()))
            self.requests.auto_choose(pending.id)

        task = asyncio.get_running_loop().create_task(wait())
        self._decision_timers[pending.id] = task
        task.add_done_callback(lambda _: self._decision_timers.pop(pending.id, None))

    def _tell_builder_the_choice(self, pending: PendingRequest, outcome: Outcome) -> None:
        """A builder's decision was answered: type the choice to it as a message (CUT-184)."""
        timer = self._decision_timers.pop(pending.id, None)
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()
        if pending.record.role != "builder" or outcome.resolution != "answered":
            return
        who = (
            "No one answered in time, so your recommended option was taken"
            if outcome.by == "timeout"
            else "The person chose"
        )
        text = f"You asked: {pending.record.detail}\n{who}: {outcome.text}"

        async def send() -> None:
            try:
                await self.send_chat(pending.project_id, text)
            except FleetError as exc:
                logger.info("the builder was not told its decision's answer: %s", exc)

        with contextlib.suppress(RuntimeError):  # no loop: nothing could be sent anyway
            self._chat_tasks.add(task := asyncio.get_running_loop().create_task(send()))
            task.add_done_callback(self._chat_tasks.discard)

    def _from_journal(self, project: Project, request_id: str) -> RequestError:
        for entry in history([*self._last_team_events(project), *self._session_events(project)]):
            if entry.raised.request_id == request_id:
                if entry.resolved is None:  # raised, never resolved, and not live: abandoned
                    return AlreadyResolvedError(Outcome(request_id, "abandoned", "system"))
                return AlreadyResolvedError(
                    Outcome(
                        request_id,
                        entry.resolved.resolution,
                        entry.resolved.by,
                        tuple(entry.resolved.rule) if entry.resolved.rule else None,
                        entry.resolved.text,
                    )
                )
        return UnknownRequestError(f"no request {request_id!r}")

    def request_history(self, project_id: str, *, limit: int = 50) -> list[HistoryEntry]:
        """The project's last team's requests that have ended, newest first."""
        project = self._projects.get(project_id)
        events = [*self._last_team_events(project), *self._session_events(project)]
        ended = [e for e in history(events) if e.resolved is not None]
        ended.sort(key=lambda entry: entry.raised_at)
        return ended[::-1][:limit]

    def sweep_abandoned(self, keep_blocked: Collection[str] = ()) -> int:
        """Resolve as `abandoned` every request a project's last team raised and never resolved.

        A pending request cannot outlive the `kopicode serve` child waiting on it, so after a
        restart any unresolved one is dead (ADR-0028). Journaled straight to each project's own
        store, like `_mark_resumed`. Returns how many were abandoned.

        ``keep_blocked`` names teams about to be resumed: their ``blocked`` requests (a stuck or
        held role, nothing waiting on them) are left open, and `_launch_team` puts them back in
        the broker, because the resumed role is still waiting for a steer. A manager's
        ``decision`` is always left open and put back by `restore_decisions` (CUT-254)."""
        count = 0
        for project in self._projects.list():
            path = Path(project.root) / ".cuttlefish" / "episodic.db"
            if not path.exists():
                continue
            # A project's last team, and its agent session (its hook cards die with the daemon too).
            scopes = [session_scope(project.id)]
            if project.last_team_id is not None:
                scopes.append(project.last_team_id)
            store = EpisodicStore.open(path)
            try:
                for scope in scopes:
                    for raised in unresolved(store.read(scope)):
                        if raised.kind == "decision" or (
                            raised.kind == "blocked" and scope in keep_blocked
                        ):
                            continue  # nothing waits on these; restore_decisions / _launch_team
                        store.append(
                            scope,
                            RequestResolved(
                                request_id=raised.request_id, resolution="abandoned", by="system"
                            ),
                        )
                        count += 1
            finally:
                store.close()
        return count

    def restore_decisions(self) -> int:
        """Put back the manager's decisions a project's agent-session journal raised and nobody
        answered (CUT-254). A decision has no process waiting on it, so a restart leaves it for
        the person; the sweep skips it. A permission card is different and is not restored: its
        hook died with the daemon, so an answer here would reach nothing."""
        count = 0
        for project in self._projects.list():
            records = [r for r in unresolved(self._session_events(project)) if r.kind == "decision"]
            if not records:
                continue
            self.open_session_journal(project)
            restored = self.requests.restore_blocked(
                project.id, session_scope(project.id), records, kind="decision"
            )
            for pending in restored:
                self._arm_decision(pending)
            count += len(restored)
        return count

    @property
    def projects(self) -> ProjectStore:
        return self._projects

    def running(self, project_id: str) -> RunningTeam | None:
        """`None` once the team's task has finished -- a finished team's own final
        state is read back from the episodic journal (`status`), not held here."""
        running = self._running.get(project_id)
        if running is not None and running.task.done():
            del self._running[project_id]
            return None
        return running

    def is_running(self, project_id: str) -> bool:
        return (
            self.running(project_id) is not None
            or self.placement.team_of_project(project_id) is not None
        )

    def is_stopping(self, project_id: str) -> bool:
        """The operator asked this project's team to stop and its round has not ended yet."""
        remote = self.placement.team_of_project(project_id)
        if remote is not None:
            return remote.stop_requested
        running = self.running(project_id)
        return running is not None and running.team_id in self._stopping

    # -- a team on a worker machine (ADR-0057, CUT-206) ----------------------------------------

    def worker_state(self, worker_id: str | None) -> tuple[str, bool] | None:
        """`(name, online)` of a worker, or `None` for no worker or one the registry lost."""
        return None if worker_id is None else self.placement.lookup(worker_id)

    def _refuse_on_worker(self, project: Project, what: str) -> None:
        if project.worker_id is not None:
            state = self.worker_state(project.worker_id)
            where = state[0] if state else "a worker machine"
            raise FleetError(
                f"{project.name} runs on {where}, and {what} is not built for a worker yet. "
                "Start a team, or move the project back to this machine."
            )

    async def _start_remote(
        self, project: Project, roles: list[RoleStart], require_approval: bool
    ) -> str:
        return await self._hand_to_worker(
            project, _build_role_inputs(project, roles), require_approval
        )

    def _worker_ready(self, project: Project) -> str:
        """The name of the worker the project is placed on, or a `FleetError` saying why a team
        cannot go there now."""
        state = self.worker_state(project.worker_id)
        if state is None:
            raise FleetError(
                f"{project.name} is placed on a worker that is no longer paired. "
                "Place it on another, or move it back to this machine."
            )
        name, online = state
        if not online:
            raise FleetError(
                f"{project.name} runs on {name}, which is offline. Start "
                f"`cuttlefish worker run` there, or move the project back to this machine."
            )
        return name

    async def _order(
        self, project: Project, team_id: str, role_inputs: list[RoleInput], require_approval: bool
    ) -> dict[str, Any]:
        """What a worker is told to run: names and settings only, never a secret or a key."""
        order: dict[str, Any] = {
            "team_id": team_id,
            "project_id": project.id,
            "name": project.name,
            "secrets_scope": project.secrets_scope,
            "backend": project.backend,
            "roles": role_inputs,
            "require_approval": require_approval,
            "repo": await asyncio.to_thread(_remote_url, project.root),
        }
        if project.max_tokens is not None:
            order["max_tokens"] = project.max_tokens
        if project.max_cost_usd is not None:
            order["max_cost_usd"] = project.max_cost_usd
        return order

    async def _hand_to_worker(
        self, project: Project, role_inputs: list[RoleInput], require_approval: bool
    ) -> str:
        """Hand a team to the worker the project is placed on. The worker takes it on its next
        poll, runs it in its own checkout, and streams its journal events back to this journal."""
        assert project.worker_id is not None
        name = self._worker_ready(project)
        await asyncio.to_thread(gitexclude.ensure_excluded, project.root)
        team_id = uuid.uuid4().hex
        order = await self._order(project, team_id, role_inputs, require_approval)
        self.placement.assign(project.id, project.worker_id, order)
        self._projects.record_team_started(
            project.id,
            team_id,
            _to_persisted_roles(role_inputs),
            require_approval=require_approval,
        )
        self._projects.set_worker_team(project.id, is_open=True)
        with logsetup.bind(project=project.id, team=team_id):
            logger.info("project %r: team handed to worker %s", project.name, name)
        return team_id

    async def adopt_remote_teams(self) -> int:
        """After a controller restart, take back the teams that were out on workers. The journal
        here is the controller's own and has everything the workers sent; what a worker still
        holds is its running process and its satay store, and the worker's next poll says which
        it still runs, so a team that is not running there is handed over again to resume. Called
        once at startup (`resume_pending`). Returns how many were taken back."""
        count = 0
        for project in self._projects.list():
            team_id = project.last_team_id
            if (
                project.worker_id is None
                or not project.worker_team_open
                or team_id is None
                or not project.last_team_roles
                or self.placement.team_of_project(project.id) is not None
            ):
                continue
            order = await self._order(
                project,
                team_id,
                _persisted_roles_to_inputs(project.last_team_roles),
                project.last_team_require_approval,
            )
            seen = await asyncio.to_thread(self._journal_count, project, team_id)
            self.placement.adopt(project.id, project.worker_id, order, seen)
            count += 1
        return count

    def _journal_count(self, project: Project, team_id: str) -> int:
        """How many of the team's events this journal holds: the worker's own position, since for
        a team out on a worker the controller appends nothing but what the worker sent."""
        path = self._journal_path(project)
        if not path.exists():
            return 0
        store = EpisodicStore.open(path)
        try:
            return sum(1 for _ in store.read(team_id))
        finally:
            store.close()

    def _journal_path(self, project: Project) -> Path:
        return Path(project.root) / ".cuttlefish" / "episodic.db"

    def _append_to_journal(
        self, project: Project, team_id: str, payloads: list[tuple[EventPayload, datetime | None]]
    ) -> None:
        store = EpisodicStore.open(self._journal_path(project))
        try:
            for payload, stamp in payloads:
                store.append(team_id, payload, ts=stamp)
        finally:
            store.close()

    def _team_of_worker(self, worker_id: str, team_id: str) -> RemoteTeam:
        team = self.placement.team(team_id)
        if team is None or team.worker_id != worker_id:
            raise FleetError(f"this controller has no team {team_id!r} on you")
        return team

    async def worker_claim(
        self, worker_id: str, wait: float, running: set[str] | None = None
    ) -> dict[str, Any]:
        return await self.placement.claim(worker_id, min(max(wait, 0.0), 20.0), running)

    async def worker_events(
        self, worker_id: str, team_id: str, events: list[dict[str, Any]]
    ) -> int:
        """Append the events a worker reports for its team to this journal. Redaction happens here,
        at write time, as for any event. Returns the position the controller has reached."""
        team = self._team_of_worker(worker_id, team_id)
        project = self._projects.get(team.project_id)
        fresh: list[tuple[EventPayload, datetime | None]] = []
        last = team.last_seq
        for item in events:
            try:
                seq = int(item["seq"])
                payload = decode_payload(str(item["type"]), item["data"])
                stamp = datetime.fromisoformat(str(item["ts"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"an event the worker sent is malformed: {exc}") from exc
            if seq > last:
                fresh.append((payload, stamp))
                last = seq
        if fresh:
            await asyncio.to_thread(self._append_to_journal, project, team_id, fresh)
            team.last_seq = last
        return team.last_seq

    async def worker_finished(
        self,
        worker_id: str,
        team_id: str,
        status: str,
        error: str | None,
        checkout: dict[str, Any] | None = None,
    ) -> None:
        """The worker says its team is over: the journal says how, and the project is free."""
        team = self._team_of_worker(worker_id, team_id)
        project = self._projects.get(team.project_id)
        tail: list[tuple[EventPayload, datetime | None]] = []
        if status == "stopped":
            tail.append((TeamStopped(), None))
        elif status == "failed":
            reason = f"on the worker: {error or 'the team failed to run'}"
            tail.append((TaskFailed(error=reason), None))
        if tail:
            await asyncio.to_thread(self._append_to_journal, project, team_id, tail)
        self.placement.end(team_id)
        name = (self.worker_state(worker_id) or ("the worker",))[0]
        self._projects.set_worker_team(
            project.id, is_open=False, handover=_handover_text(name, checkout)
        )
        with logsetup.bind(project=project.id, team=team_id):
            logger.info("worker team ended (%s)", status)

    async def worker_removed(self, worker_id: str, name: str = "a worker") -> Sequence[str]:
        """A worker was removed: what ran on it ends here, and its projects run on this machine
        again (they are not on any worker until a person places them). Each shows a Needs you
        card saying so, and carries a warning about what the machine may still hold. Returns
        their ids."""
        for team in self.placement.teams_on(worker_id):
            await self._end_here(team)
        placed = [p for p in self._projects.list() if p.worker_id == worker_id]
        unplaced = self._projects.unplace_worker(worker_id)
        for project in placed:
            if project.last_team_id is not None:
                self._projects.set_worker_team(
                    project.id,
                    handover=f"{name} was removed, so work it did not push to the remote is "
                    "not reachable from here.",
                )
            self.raise_decision(
                project.id,
                f"{project.name} was on {name}, which was removed. It now runs on this machine. "
                "Keep it here, or place it on another worker?",
                [_KEEP_HERE, _PLACE_ELSEWHERE],
                _KEEP_HERE,
                role="system",
            )
        return unplaced

    async def _end_here(self, team: RemoteTeam) -> None:
        """End a team that nothing will report on any more, in the journal and here."""
        project = self._projects.get(team.project_id)
        stopped: list[tuple[EventPayload, datetime | None]] = [
            (StopRequested(), None),
            (TeamStopped(), None),
        ]
        await asyncio.to_thread(self._append_to_journal, project, team.team_id, stopped)
        self.placement.end(team.team_id)
        name = (self.worker_state(team.worker_id) or ("the worker",))[0]
        self._projects.set_worker_team(project.id, is_open=False, handover=_never_reported(name))

    async def _stop_remote(self, team: RemoteTeam) -> None:
        state = self.worker_state(team.worker_id)
        if team.order is not None or state is None or not state[1]:
            # The worker never took it, or is gone: nothing there will report, so end it here.
            await self._end_here(team)
            return
        team.stop_requested = True

    def _check_handover(self, project: Project, acknowledge: bool) -> None:
        if project.worker_handover and not acknowledge:
            raise UnpushedWorkError(project.worker_handover)

    async def place(
        self, project_id: str, worker_id: str | None, *, acknowledge: bool = False
    ) -> Project:
        """Run the project's next teams on a worker (or, `None`, here). Not while a team runs."""
        project = self._projects.get(project_id)
        if self.is_running(project_id):
            raise FleetError("this project has a running team; stop it first, or move it")
        if worker_id == project.worker_id:
            return project
        self._check_handover(project, acknowledge)
        placed = self._projects.update_worker(project_id, worker_id)
        self._projects.set_worker_team(project_id, is_open=False, handover=None)
        return placed

    async def move(
        self,
        project_id: str,
        worker_id: str | None,
        *,
        acknowledge: bool = False,
        restart: bool = False,
        stop_wait: float = 120.0,
    ) -> tuple[Project, str | None]:
        """Move the project to another worker (or, `None`, back here), carrying on a team that was
        running (or, with `restart`, one that a refused first try of this move stopped). Returns
        the project and the new team's id (`None` if nothing was carried on).

        What moves is the project's place and its next team, not a live agent: the journal is the
        controller's and stays; the code goes by the remote (the new machine clones `origin`), so
        work the old one did not push is warned about first and never silently dropped; and the
        new team starts the persisted roles again, a new round on the new machine, because the
        old worker's satay run lives in its own store and cannot follow. Everything that can be
        refused is checked before the running team is stopped, so a refused move leaves it
        running."""
        project = self._projects.get(project_id)
        if worker_id == project.worker_id:
            raise FleetError("the project is already there")
        if worker_id is not None:
            state = self.worker_state(worker_id)
            if state is None:
                raise FleetError("there is no such worker; pair one under Devices")
            if not state[1]:
                raise FleetError(f"{state[0]} is offline, so the project cannot move there now")
        self._check_handover(project, acknowledge)
        roles, approval = project.last_team_roles, project.last_team_require_approval
        was_running = self.is_running(project_id)
        remote = self.placement.team_of_project(project_id)
        if remote is not None and not acknowledge:
            state = self.worker_state(remote.worker_id)
            if state is None or not state[1]:
                # Nothing there will report its checkout, so say so now, while it still runs.
                raise UnpushedWorkError(_never_reported(state[0] if state else "its worker"))
        if was_running:
            await self.stop(project_id)
            deadline = asyncio.get_running_loop().time() + stop_wait
            while self.is_running(project_id):
                if asyncio.get_running_loop().time() > deadline:
                    raise FleetError(
                        "the team was told to stop but is still ending; the project has not "
                        "moved. Move it again once it has stopped"
                    )
                await asyncio.sleep(0.2)
            # The worker's last word about its checkout arrives with the stop.
            try:
                self._check_handover(self._projects.get(project_id), acknowledge)
            except UnpushedWorkError as exc:
                raise UnpushedWorkError(
                    f"{exc} The team has stopped; the project stays where it is until you "
                    "move it again."
                ) from exc
        self._projects.update_worker(project_id, worker_id)
        self._projects.set_worker_team(project_id, is_open=False, handover=None)
        moved = self._projects.get(project_id)
        team_id: str | None = None
        if (was_running or restart) and roles:
            inputs = _persisted_roles_to_inputs(roles)
            if moved.worker_id is not None:
                team_id = await self._hand_to_worker(moved, inputs, approval)
            else:
                team_id = uuid.uuid4().hex
                await asyncio.to_thread(gitexclude.ensure_excluded, moved.root)
                await self._launch_team(moved, team_id, inputs, require_approval=approval)
                self._projects.record_team_started(
                    project_id, team_id, roles, require_approval=approval
                )
        return moved, team_id

    async def start(
        self,
        project_id: str,
        roles: list[RoleStart],
        *,
        require_approval: bool = False,
        prepare: Literal["yes", "skip"] | None = None,
    ) -> str:
        """Start `project_id`'s team with `roles`. Returns the new team id.

        Every daemon-launched team is unconditionally steerable (ADR-0008's own
        plumbing, reused as-is) -- the dashboard's whole point is a live chat panel
        per role. A daemon-started team declares no project secrets beyond a
        backend's own ambient credential names (`AgentBackend.CREDENTIAL_ENV_VARS`)
        -- a project needing `--secret`-declared names still runs via the CLI
        directly this slice, a real, named simplification, not an oversight.

        `require_approval` (KAN-1711) is team-wide, opt-in per start call (unlike
        `steerable`, which every daemon-started team already gets unconditionally)
        -- an operator choosing whether *this* run needs a formal review gate, not
        a project-wide default.

        `prepare` (V5-E3, ADR-0029) decides what happens when the project's dependencies need
        installing: `yes` installs them first (inside the team's own lifecycle, journaled, outside
        any agent turn), `skip` starts without. Left out, the project's `env_prepare` decides:
        `auto` installs, `off` never does, and `ask` refuses with `EnvironmentConfirmationError`
        so the caller (a person, an MCP client) says which.
        """
        project = self._projects.get(project_id)
        if self.is_running(project_id):
            raise FleetError(f"project {project_id!r} already has a running team")
        known = [r.name for r in project.roles]
        unknown = [r["name"] for r in roles if known and r["name"] not in known]
        if unknown:
            raise UnknownRoleError(
                f"unknown role {', '.join(repr(n) for n in unknown)}; "
                f"this project's roles are: {', '.join(known)}"
            )

        if project.worker_id is not None:
            return await self._start_remote(project, roles, require_approval)
        await asyncio.to_thread(gitexclude.ensure_excluded, project.root)
        spec = await asyncio.to_thread(environment.detect, project.root)
        steps = await self._environment_steps(project, prepare, spec)
        env_note = environment.brief(spec, installing={step.ecosystem for step in steps})
        team_id = uuid.uuid4().hex
        role_inputs = _build_role_inputs(project, roles, env_note)
        await self._launch_team(
            project, team_id, role_inputs, require_approval=require_approval, prepare_steps=steps
        )
        self._projects.record_team_started(
            project_id,
            team_id,
            _to_persisted_roles(role_inputs),
            require_approval=require_approval,
        )
        return team_id

    async def _environment_steps(
        self,
        project: Project,
        prepare: Literal["yes", "skip"] | None,
        spec: environment.EnvironmentSpec,
    ) -> tuple[envprep.PrepareStep, ...]:
        """The install steps this start should run first, or none. Reads files only (off the
        event loop: the root may be on a slow mount); also remembers installs a person made
        themselves, so a later change to their files is noticed."""
        found = await asyncio.to_thread(envprep.plan, project.root, spec)
        await asyncio.to_thread(envprep.record_adopted, project.root, found)
        if not found.steps or prepare == "skip" or project.env_prepare == "off":
            return ()
        if prepare == "yes" or project.env_prepare == "auto":
            return found.steps
        raise EnvironmentConfirmationError(project.id, found)

    async def _prepare_environment(
        self,
        store: EpisodicStore,
        team_id: str,
        project: Project,
        role_names: Sequence[str],
        steps: Sequence[envprep.PrepareStep],
    ) -> bool:
        """Run `steps` before the team's first round, journaling each. False when the team
        must not go on: a step failed (every role is then recorded failed, with why, so the
        dashboard says so) or a stop was asked for (the caller records `TeamStopped`)."""
        cancel = asyncio.Event()
        self._preparing[team_id] = cancel
        try:
            for step in steps:
                store.append(
                    team_id,
                    EnvironmentPrepareStarted(
                        ecosystem=step.ecosystem,
                        path=step.path,
                        commands=[list(c) for c in step.commands],
                        reason=step.reason,
                    ),
                )
                result = await envprep.run_step(step, project.root, cancel=cancel)
                store.append(
                    team_id,
                    EnvironmentPrepared(
                        ecosystem=step.ecosystem,
                        path=step.path,
                        ok=result.ok,
                        exit_code=result.exit_code,
                        duration_s=round(result.duration_s, 2),
                        tail=result.tail,
                        failure=result.failure,
                    ),
                )
                # Whatever a failed or interrupted install left behind is not to be trusted (a
                # half-made .venv looks installed): remember, so the next start installs again.
                # A missing tool installed nothing, so there is nothing to distrust.
                if not result.ok and result.failure != "tool_missing":
                    await asyncio.to_thread(
                        envprep.write_state,
                        project.root,
                        envprep.state_key(step.ecosystem, step.path),
                        fingerprint=step.fingerprint,
                        how="failed",
                    )
                if result.cancelled:
                    return False
                if not result.ok:
                    why = _install_failure_text(step.ecosystem, result, step.path)
                    for name in role_names:
                        store.append(team_id, TaskFailed(error=why, role=name))
                    return False
                await asyncio.to_thread(
                    envprep.write_state,
                    project.root,
                    envprep.state_key(step.ecosystem, step.path),
                    # What is on disk now: an install may have written its own lockfile.
                    fingerprint=envprep.fingerprint_now(
                        project.root, step.ecosystem, default=step.fingerprint, path=step.path
                    ),
                    how="prepared",
                    produced=envprep.produced_env(project.root, step.ecosystem, step.path),
                )
            return True
        finally:
            self._preparing.pop(team_id, None)

    async def _launch_team(
        self,
        project: Project,
        team_id: str,
        role_inputs: list[RoleInput],
        *,
        require_approval: bool = False,
        prepare_steps: Sequence[envprep.PrepareStep] = (),
    ) -> None:
        """Drive `run_team` for `project` under `team_id`/`role_inputs`, shared by
        `start` (a fresh `team_id`, never seen by satay before) and `resume_pending`
        (an existing, persisted `team_id`/`role_inputs`) -- `satay.start` itself
        resolves create-vs-resume purely from whether that `run_id`'s row already
        exists in `project`'s own `.satay` store (ADR-0010), so this method doesn't
        need to know or care which case it's in.
        """
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[tuple[str, str]] = loop.create_future()

        async def _drive() -> None:
            prepared: PreparedRun | None = None
            # This task's whole life: every log line it and its children write names this
            # project and team (a `contextvars` copy, so a concurrent team's own never mixes in).
            logsetup.set_context(project=project.id, team=team_id)
            try:
                # Every secret the project can use goes to its agents, as environment variables
                # (decided at this start: a change applies to the next one).
                prepared = prepare_run(
                    project=project.secrets_scope,
                    secret_names=self.project_secret_names(project),
                    base_dir=Path(project.root),
                    agent_backend=project.backend,
                    extra_backends=[r["backend"] for r in role_inputs if r.get("backend")],
                    central_secrets_db=self.secrets_path(),
                )
                self._team_stores[team_id] = prepared.episodic_store
                events = list(prepared.episodic_store.read(team_id))
                self.requests.seed_grants(team_id, granted_rules(events))
                # A resumed role may still be held for a person (a Stuck card): put it back.
                self.requests.restore_blocked(project.id, team_id, unresolved(events))
                runtime.configure(
                    dataclasses.replace(
                        prepared.as_runtime(),
                        requests=RequestContext(
                            self.requests, project.id, team_id, self._request_window_s
                        ),
                        broker=await self.ensure_broker(),
                    )
                )
                workflow_input: TeamInput = {
                    "team_id": team_id,
                    "root": project.root,
                    "project": project.secrets_scope,
                    "roles": role_inputs,
                    "steerable": True,
                    "require_approval": require_approval,
                }
                if project.max_tokens is not None:
                    workflow_input["max_tokens"] = project.max_tokens
                if project.max_cost_usd is not None:
                    workflow_input["max_cost_usd"] = project.max_cost_usd
                async with satay.control.run_app(data_dir=Path(project.root) / ".satay") as app:
                    if not ready.done():
                        ready.set_result((app.base_url, app.token))
                    if prepare_steps and not await self._prepare_environment(
                        prepared.episodic_store,
                        team_id,
                        project,
                        [r["name"] for r in role_inputs],
                        prepare_steps,
                    ):
                        return
                    handle = satay.start(run_team, workflow_input, run_id=team_id, store=app.store)
                    # The episodic journal already recorded why (Q16's posture) --
                    # nothing further for the daemon to do with a failed team.
                    try:
                        await handle.result()
                    except satay.WorkflowFailedError:
                        pass
                    except RuntimeError:
                        # A stop cancels the run between rounds, so satay finds it without a
                        # terminal event: expected after a stop, a real error otherwise.
                        if team_id not in self._stopping:
                            raise
            except Exception as exc:
                if not ready.done():
                    logger.error("team failed to start: %s", exc, exc_info=True)
                    # Nothing started -- report the failure through `ready` instead
                    # of leaving it an unretrieved task exception (a second,
                    # redundant warning for the identical failure).
                    ready.set_exception(exc)
                    return
                raise
            finally:
                logger.info(
                    "team %s ended%s", team_id, " (stopped)" if team_id in self._stopping else ""
                )
                if prepared is not None:
                    # Whatever a person was still being asked ends with the team, journaled
                    # while its store is open.
                    self.requests.end_team(team_id, "abandoned")
                    if team_id in self._stopping:
                        prepared.episodic_store.append(team_id, TeamStopped())
                    self._stopping.discard(team_id)
                    self._team_stores.pop(team_id, None)
                    prepared.close()

        task = asyncio.create_task(_drive())
        try:
            base_url, token = await ready
        except Exception as exc:
            raise FleetError(f"project {project.id!r} failed to start: {exc}") from exc

        self._running[project.id] = RunningTeam(
            team_id=team_id, base_url=base_url, token=token, task=task
        )
        with logsetup.bind(project=project.id, team=team_id):
            logger.info(
                "project %r: team started, roles=%s, root=%s",
                project.name,
                ",".join(r["name"] for r in role_inputs),
                project.root,
            )

    async def _is_resumable(self, project: Project, team_id: str) -> bool:
        """Whether `team_id`'s satay run is still non-terminal -- a plain, read-only
        open of `project`'s own `.satay/satay.db` (WAL mode makes this safe even
        while a *different* project's own writer is live, the same posture
        `_last_team_events` already holds for the episodic store). `False` for a
        project that never actually reached satay (no `.satay` yet), not an error."""
        database = satay_db_path(Path(project.root) / ".satay")
        if not database.exists():
            return False
        store = SQLiteStore.open(database)
        try:
            record = await store.get_run(team_id)
        finally:
            store.close()
        return record is not None and record.status not in TERMINAL_STATUSES

    def _mark_resumed(self, project: Project, team_id: str) -> None:
        """Journal a `TeamResumed` marker directly to `project`'s own episodic
        store, not through the durable `journal` task -- there is no workflow
        context at this call site, the same "write straight to the store"
        posture `_last_team_events` already holds to for reads. `resumed_from_seq`
        is the highest seq the journal already held, so this renders as exactly
        "resumed after crash at seq N" (ADR-0010/KAN-1705) -- distinct from
        ordinary progress, so an operator can see continuity actually working
        instead of just trusting it."""
        episodic_path = Path(project.root) / ".cuttlefish" / "episodic.db"
        store = EpisodicStore.open(episodic_path)
        try:
            last_seq = 0
            for event in store.read(team_id):
                last_seq = event.seq
            store.append(team_id, TeamResumed(resumed_from_seq=last_seq))
        finally:
            store.close()

    async def resume_pending(self) -> list[ResumeAttempt]:
        """Resume every registered project's last team that was still running when
        the daemon (or its host process) died -- ADR-0009's own named daemon-restart
        gap, closed here per ADR-0010: re-drive `run_team` with the *same*
        `team_id`/`role_inputs` its last start used, which satay's own
        `RunController.result()` already resumes correctly (it's the identical
        `satay.start(..., run_id=)` call `start` makes for a brand-new team --
        satay itself decides create-vs-resume from whether that id's row already
        exists). Call once, at `cuttlefish serve` startup, before the HTTP surface
        opens.

        A project with no persisted `last_team_roles` (registered but never
        started, or started before this fix shipped) is skipped -- there is no
        durable record of the `TeamInput` its last run actually used, and passing a
        *different* one would risk satay's own strict `nondeterminism` policy
        rejecting the resume rather than silently corrupting it. A project whose
        last team already reached a terminal state is skipped too -- nothing to
        resume.
        """
        resumable: list[tuple[Project, str]] = []
        for project in self._projects.list():
            team_id = project.last_team_id
            if team_id is None or not project.last_team_roles or self.is_running(project.id):
                continue
            if await self._is_resumable(project, team_id):
                resumable.append((project, team_id))
        self.sweep_abandoned(keep_blocked={team_id for _, team_id in resumable})
        self.restore_decisions()
        await self.adopt_remote_teams()
        attempts: list[ResumeAttempt] = []
        for project, team_id in resumable:
            role_inputs = _persisted_roles_to_inputs(project.last_team_roles)
            self._mark_resumed(project, team_id)
            try:
                await self._launch_team(
                    project,
                    team_id,
                    role_inputs,
                    require_approval=project.last_team_require_approval,
                )
            except FleetError as exc:
                attempts.append(ResumeAttempt(project.id, project.name, team_id, error=str(exc)))
                continue
            attempts.append(ResumeAttempt(project.id, project.name, team_id, error=None))
        return attempts

    async def kill(
        self, project_id: str, *, scope: Literal["project", "all"] = "project"
    ) -> list[str]:
        """The kill switch for one project (CUT-186): stop its team and its builder session, and
        journal that it was pressed. Returns what was stopped (``team``, ``builder``). The builder
        session is gone at once; a team gets its stop request at once and ends when its round does
        (see `stop`). Journals nothing for a project of ``scope="all"`` that was idle."""
        project = self._projects.get(project_id)
        stopped: list[str] = []
        if self.is_running(project_id):
            with contextlib.suppress(FleetError):  # it ended while we looked
                await self.stop(project_id)
                stopped.append("team")
        info = await asyncio.to_thread(self._session_manager().status, project.id, project.root)
        if info.state == "running":
            await self.stop_agent_session(project_id)
            stopped.append("builder")
        if stopped or scope == "project":
            self.append_session_event(project, KillSwitchPressed(scope, stopped))
        return stopped

    async def kill_all(self) -> dict[str, list[str]]:
        """The kill switch for every project at once, each stopped at the same time. A project
        that fails to stop is reported with no entry and does not hold the others back."""
        projects = self._projects.list()
        results = await asyncio.gather(
            *(self.kill(p.id, scope="all") for p in projects), return_exceptions=True
        )
        for project, result in zip(projects, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning("kill switch could not stop %s: %s", project.name, result)
        return {
            p.id: r for p, r in zip(projects, results, strict=True) if isinstance(r, list) and r
        }

    async def stop(self, project_id: str) -> None:
        """`asyncio.to_thread` is load-bearing, not a style choice: `cancel_run` is a
        blocking `urllib` call against a `satay.control.run_app` server running in
        this *same* process, on this *same* event loop (the daemon drives every
        team in-process, ADR-0009) -- calling it directly would block the loop that
        the target server itself needs to answer the request, deadlocking against
        itself exactly as `cuttlefish.steering.send_steering_message`'s own
        docstring warns (`cuttlefish steer`, a separate short-lived CLI process,
        never hits this because it never shares a loop with what it's calling)."""
        remote = self.placement.team_of_project(project_id)
        if remote is not None:
            await self._stop_remote(remote)
            return
        running = self.running(project_id)
        if running is None:
            raise FleetError(f"project {project_id!r} has no running team")
        preparing = self._preparing.get(running.team_id)
        if preparing is not None:
            # Still installing dependencies: there is no satay run to cancel yet, so stop the
            # install itself. `_drive` then ends the team and journals `TeamStopped`.
            self._stopping.add(running.team_id)
            preparing.set()
            with logsetup.bind(project=project_id, team=running.team_id):
                logger.info("stop requested while preparing the environment")
            return
        try:
            await asyncio.to_thread(
                cancel_run, base_url=running.base_url, token=running.token, run_id=running.team_id
            )
        except SteeringDeliveryError as exc:
            raise FleetError(str(exc)) from exc
        # Only once the cancel was accepted, so a failed Stop leaves the team as it was. The
        # workflow reads this at its next round boundary and drops a steer still queued instead
        # of starting one more round before satay's cancel is seen (CUT-107).
        if running.team_id in self._team_stores:
            self._append_for_team(running.team_id, StopRequested())
        # satay's cancel only lands when the round ends, and a round held open for a person
        # would not end. Tell the waiting agent no, and every later request no too, so it can
        # (ADR-0028); `is_stopping` lets the dashboard say "stopping" at once, not "working".
        self._stopping.add(running.team_id)
        self.requests.end_team(running.team_id, "cancelled")
        # A Codex role mid-turn is told to end the turn now (`turn/interrupt`, CUT-166), so Stop
        # does not wait out a round that can run for minutes. A turn that does not confirm in time
        # is left to the round boundary, where satay's cancel lands as before.
        interrupted = await codex_live.interrupt_root(self._projects.get(project_id).root)
        with logsetup.bind(project=project_id, team=running.team_id):
            logger.info("stop requested (%d codex turn(s) interrupted)", interrupted)

    async def steer(self, project_id: str, role: str | None, text: str) -> None:
        """See `stop`'s own docstring -- `asyncio.to_thread` for the identical
        same-loop-deadlock reason."""
        if self.placement.team_of_project(project_id) is not None:
            raise FleetError(
                "this team runs on a worker, and steering one is not built yet; "
                "stop it and start it again with the new instruction"
            )
        running = self.running(project_id)
        if running is None:
            # No team: the builder is an agent session, which a message reaches as chat (ADR-0044).
            project = self._projects.get(project_id)
            info = await asyncio.to_thread(self._session_manager().status, project.id, project.root)
            if info.state != "running":
                raise FleetError(
                    f"{project.name} has no running team and its builder session is not "
                    "running, so there is nobody to message"
                )
            await self.send_chat(project_id, text)
            return
        if not role:
            raise FleetError("this project has a running team: say which role to message")
        # A Codex role mid-turn takes the message at once (ADR-0033); anything else, or a turn
        # that has just ended, is queued for the round boundary as before.
        if await codex_live.steer(self._projects.get(project_id).root, role, text):
            if running.team_id in self._team_stores:
                self._append_for_team(running.team_id, SteeringQueued(text=text, role=role))
                self._append_for_team(running.team_id, SteeringMessage(text=text, role=role))
            self.requests.supersede(running.team_id, role)
            return
        try:
            await asyncio.to_thread(
                send_steering_message,
                base_url=running.base_url,
                token=running.token,
                task_id=running.team_id,
                role=role,
                text=text,
            )
        except SteeringDeliveryError as exc:
            raise FleetError(str(exc)) from exc
        if running.team_id in self._team_stores:
            self._append_for_team(running.team_id, SteeringQueued(text=text, role=role))
        self.requests.supersede(running.team_id, role)

    async def approve(
        self, project_id: str, role: str, *, approved: bool, comment: str | None = None
    ) -> None:
        """Deliver one approve/reject decision to `project_id`'s running team
        (KAN-1711) -- see `stop`'s own docstring for the identical
        `asyncio.to_thread` same-loop-deadlock reason."""
        if self.placement.team_of_project(project_id) is not None:
            raise FleetError("this team runs on a worker, and a review gate there is not built yet")
        running = self.running(project_id)
        if running is None:
            raise FleetError(f"project {project_id!r} has no running team to decide on")
        try:
            await asyncio.to_thread(
                send_approval_decision,
                base_url=running.base_url,
                token=running.token,
                task_id=running.team_id,
                role=role,
                approved=approved,
                comment=comment,
            )
        except SteeringDeliveryError as exc:
            raise FleetError(str(exc)) from exc
        self.requests.supersede(running.team_id, role)

    def _last_team_events(self, project: Project) -> list[EpisodicEvent]:
        """Every event of `project.last_team_id`, or `[]` if there isn't one yet or
        its journal hasn't been created yet -- read-only, opening its own connection
        (the store's own WAL mode makes this safe alongside the writer
        `_drive` holds while a team is running)."""
        if project.last_team_id is None:
            return []
        episodic_path = Path(project.root) / ".cuttlefish" / "episodic.db"
        if not episodic_path.exists():
            return []
        store = EpisodicStore.open(episodic_path)
        try:
            return list(store.read(project.last_team_id))
        finally:
            store.close()

    def status(self, project_id: str) -> dict[str, RoleStatus]:
        return self.journal_view(project_id).status

    def usage(self, project_id: str) -> dict[str, UsageTotals]:
        """`role name -> its cumulative usage so far` (KAN-1712/ADR-0017), for
        `project_id`'s last team -- derived from the identical journal `status`
        already reads, never a second store."""
        return self.journal_view(project_id).usage

    def last_event_at(self, project_id: str) -> str | None:
        """When `project_id`'s last team's journal last changed (ISO 8601), for the portfolio
        card's "active 12 s ago"; `None` before a team has ever run."""
        return self.journal_view(project_id).last_event_at

    def journal_view(self, project_id: str) -> JournalView:
        """Status, usage and last event time from ONE read of the journal. The project list asks
        for all three for every project on every poll, and a read opens the SQLite file: three
        reads each made a 100-project list three times slower (CUT-192)."""
        project = self._projects.get(project_id)
        role_names = [r.name for r in project.roles]
        events = self._last_team_events(project)
        names = role_names or sorted(roles_in(events))
        payloads = [event.payload for event in events]
        return JournalView(
            status=role_statuses(events, names) if events else dict.fromkeys(role_names, "queued"),
            usage={name: cumulative_usage(payloads, role=name) for name in names},
            last_event_at=events[-1].ts.isoformat() if events else None,
        )

    def events(self, project_id: str) -> list[EpisodicEvent]:
        """`project_id`'s last team's full episodic record, in order -- the same
        journal `cuttlefish show` reads, for the dashboard's own event-tail view."""
        project = self._projects.get(project_id)
        return self._last_team_events(project)


def _remote_url(root: str) -> str | None:
    """The project's `origin` for a worker to clone, with no credential in it, or `None`."""
    try:
        out = subprocess.run(
            ["git", "-C", root, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if "://" in out:
        parts = urlsplit(out)
        if parts.username or parts.password:
            host = parts.hostname or ""
            netloc = f"{host}:{parts.port}" if parts.port else host
            out = urlunsplit((parts.scheme, netloc, *parts[2:]))
    return out or None


def _secret_rows(names: Sequence[str]) -> list[dict[str, str]]:
    from cuttlefish.agents.registry import credential_names

    credentials = credential_names()
    return [
        {"name": name, "kind": "credential" if name in credentials else "secret"} for name in names
    ]


def _parse_stamp(stamp: str) -> float:
    """A session record's ISO time as seconds since the epoch, or 0 when it cannot be read."""
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
