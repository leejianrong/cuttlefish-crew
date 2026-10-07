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
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

import satay
import satay.control
from satay.config import db_path as satay_db_path
from satay.journal.events import TERMINAL_STATUSES
from satay.journal.store import SQLiteStore

from cuttlefish import environment, envprep, logsetup, runtime
from cuttlefish.budget import UsageTotals, cumulative_usage
from cuttlefish.config import PreparedRun, prepare_run
from cuttlefish.delegate.presets import DEFAULT_PRESETS
from cuttlefish.episodic.events import (
    EnvironmentPrepared,
    EnvironmentPrepareStarted,
    EventPayload,
    RequestResolved,
    TaskFailed,
    TeamResumed,
    TeamStopped,
)
from cuttlefish.episodic.store import EpisodicEvent, EpisodicStore
from cuttlefish.fleet.status import RoleStatus, role_statuses, roles_in
from cuttlefish.permissions import DEFAULT_MODE, effective_access
from cuttlefish.projects.store import PersistedRole, Project, ProjectStore, RoleDefinition
from cuttlefish.requests import (
    AlreadyResolvedError,
    HistoryEntry,
    Outcome,
    RequestBroker,
    RequestContext,
    RequestError,
    UnknownRequestError,
    granted_rules,
    history,
    unresolved,
)
from cuttlefish.steering import (
    SteeringDeliveryError,
    cancel_run,
    send_approval_decision,
    send_steering_message,
)
from cuttlefish.team import RoleInput, TeamInput, run_team

logger = logging.getLogger(__name__)


class FleetError(Exception):
    """A fleet-daemon-level operation failed, distinctly from a workflow's own
    failure (e.g. starting a project that already has a team running)."""


class EnvironmentConfirmationError(FleetError):
    """Dependencies need installing and nobody has said whether to (ADR-0029, V5-E3): the
    project's `env_prepare` is `ask` and the start call named no `prepare`."""

    def __init__(self, project_id: str, plan: envprep.PreparePlan) -> None:
        names = ", ".join(f"{s.ecosystem} ({s.reason})" for s in plan.steps)
        super().__init__(
            f"project {project_id!r}: its dependencies need installing first: {names}. "
            "Start again with prepare=yes to install them now, prepare=skip to start without "
            "them, or set the project's environment setting to auto."
        )
        self.plan = plan


def _install_failure_text(ecosystem: str, result: envprep.StepResult) -> str:
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
        f"couldn't install the {ecosystem} dependencies ({cause}). "
        "The install row above has the output; start with prepare=skip to go without."
    )


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
    ) -> None:
        self._projects = project_store
        self._running: dict[str, RunningTeam] = {}
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

    def _append_for_team(self, team_id: str, payload: EventPayload) -> EpisodicEvent:
        return self._team_stores[team_id].append(team_id, payload)

    def answer_request(
        self,
        project_id: str,
        request_id: str,
        answer: str,
        *,
        rule: Sequence[str] | None = None,
    ) -> Outcome:
        """Answer a pending request of `project_id` (ADR-0028). Raises the broker's
        `RequestError`s. An "Always allow" is applied to the running team by the broker and
        saved to the project's own commands here, for the starts after this one."""
        project = self._projects.get(project_id)
        try:
            outcome = self.requests.answer(request_id, answer, rule=rule, project_id=project_id)
        except UnknownRequestError:
            # Not in memory: a request from before a restart, or from a finished team. The
            # journal still says how it ended.
            raise self._from_journal(project, request_id) from None
        if outcome.rule is not None:
            self._projects.add_allow(project_id, outcome.rule)
        return outcome

    def _from_journal(self, project: Project, request_id: str) -> RequestError:
        for entry in history(self._last_team_events(project)):
            if entry.raised.request_id == request_id:
                if entry.resolved is None:  # raised, never resolved, and not live: abandoned
                    return AlreadyResolvedError(Outcome(request_id, "abandoned", "system"))
                return AlreadyResolvedError(
                    Outcome(
                        request_id,
                        entry.resolved.resolution,
                        entry.resolved.by,
                        tuple(entry.resolved.rule) if entry.resolved.rule else None,
                    )
                )
        return UnknownRequestError(f"no request {request_id!r}")

    def request_history(self, project_id: str, *, limit: int = 50) -> list[HistoryEntry]:
        """The project's last team's requests that have ended, newest first."""
        project = self._projects.get(project_id)
        ended = [e for e in history(self._last_team_events(project)) if e.resolved is not None]
        return ended[::-1][:limit]

    def sweep_abandoned(self) -> int:
        """Resolve as `abandoned` every request a project's last team raised and never resolved.

        A pending request cannot outlive the `kopicode serve` child waiting on it, so after a
        restart any unresolved one is dead (ADR-0028). Journaled straight to each project's own
        store, like `_mark_resumed`. Returns how many were abandoned."""
        count = 0
        for project in self._projects.list():
            path = Path(project.root) / ".cuttlefish" / "episodic.db"
            if project.last_team_id is None or not path.exists():
                continue
            store = EpisodicStore.open(path)
            try:
                for raised in unresolved(store.read(project.last_team_id)):
                    store.append(
                        project.last_team_id,
                        RequestResolved(
                            request_id=raised.request_id, resolution="abandoned", by="system"
                        ),
                    )
                    count += 1
            finally:
                store.close()
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
        return self.running(project_id) is not None

    def is_stopping(self, project_id: str) -> bool:
        """The operator asked this project's team to stop and its round has not ended yet."""
        running = self.running(project_id)
        return running is not None and running.team_id in self._stopping

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
                        commands=[list(c) for c in step.commands],
                        reason=step.reason,
                    ),
                )
                result = await envprep.run_step(step, project.root, cancel=cancel)
                store.append(
                    team_id,
                    EnvironmentPrepared(
                        ecosystem=step.ecosystem,
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
                        step.ecosystem,
                        fingerprint=step.fingerprint,
                        how="failed",
                    )
                if result.cancelled:
                    return False
                if not result.ok:
                    why = _install_failure_text(step.ecosystem, result)
                    for name in role_names:
                        store.append(team_id, TaskFailed(error=why, role=name))
                    return False
                await asyncio.to_thread(
                    envprep.write_state,
                    project.root,
                    step.ecosystem,
                    # What is on disk now: an install may have written its own lockfile.
                    fingerprint=envprep.fingerprint_now(
                        project.root, step.ecosystem, default=step.fingerprint
                    ),
                    how="prepared",
                    produced=envprep.produced_env(project.root, step.ecosystem),
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
                prepared = prepare_run(
                    project=project.secrets_scope,
                    secret_names=[],
                    base_dir=Path(project.root),
                    agent_backend=project.backend,
                    extra_backends=[r["backend"] for r in role_inputs if r.get("backend")],
                )
                self._team_stores[team_id] = prepared.episodic_store
                self.requests.seed_grants(
                    team_id, granted_rules(prepared.episodic_store.read(team_id))
                )
                runtime.configure(
                    dataclasses.replace(
                        prepared.as_runtime(),
                        requests=RequestContext(
                            self.requests, project.id, team_id, self._request_window_s
                        ),
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
                    with contextlib.suppress(satay.WorkflowFailedError):
                        await handle.result()
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
        self.sweep_abandoned()
        attempts: list[ResumeAttempt] = []
        for project in self._projects.list():
            team_id = project.last_team_id
            if team_id is None or not project.last_team_roles or self.is_running(project.id):
                continue
            if not await self._is_resumable(project, team_id):
                continue
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

    async def stop(self, project_id: str) -> None:
        """`asyncio.to_thread` is load-bearing, not a style choice: `cancel_run` is a
        blocking `urllib` call against a `satay.control.run_app` server running in
        this *same* process, on this *same* event loop (the daemon drives every
        team in-process, ADR-0009) -- calling it directly would block the loop that
        the target server itself needs to answer the request, deadlocking against
        itself exactly as `cuttlefish.steering.send_steering_message`'s own
        docstring warns (`cuttlefish steer`, a separate short-lived CLI process,
        never hits this because it never shares a loop with what it's calling)."""
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
        # satay's cancel only lands when the round ends, and a round held open for a person
        # would not end. Tell the waiting agent no, and every later request no too, so it can
        # (ADR-0028); `is_stopping` lets the dashboard say "stopping" at once, not "working".
        self._stopping.add(running.team_id)
        self.requests.end_team(running.team_id, "cancelled")
        with logsetup.bind(project=project_id, team=running.team_id):
            logger.info("stop requested")

    async def steer(self, project_id: str, role: str, text: str) -> None:
        """See `stop`'s own docstring -- `asyncio.to_thread` for the identical
        same-loop-deadlock reason."""
        running = self.running(project_id)
        if running is None:
            raise FleetError(f"project {project_id!r} has no running team to steer")
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
        self.requests.supersede(running.team_id, role)

    async def approve(
        self, project_id: str, role: str, *, approved: bool, comment: str | None = None
    ) -> None:
        """Deliver one approve/reject decision to `project_id`'s running team
        (KAN-1711) -- see `stop`'s own docstring for the identical
        `asyncio.to_thread` same-loop-deadlock reason."""
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
        project = self._projects.get(project_id)
        role_names = [r.name for r in project.roles]
        events = self._last_team_events(project)
        if not events:
            return dict.fromkeys(role_names, "queued")
        names = role_names or sorted(roles_in(events))
        return role_statuses(events, names)

    def usage(self, project_id: str) -> dict[str, UsageTotals]:
        """`role name -> its cumulative usage so far` (KAN-1712/ADR-0017), for
        `project_id`'s last team -- derived from the identical journal `status`
        already reads, never a second store."""
        project = self._projects.get(project_id)
        role_names = [r.name for r in project.roles]
        events = self._last_team_events(project)
        names = role_names or sorted(roles_in(events))
        payloads = [event.payload for event in events]
        return {name: cumulative_usage(payloads, role=name) for name in names}

    def events(self, project_id: str) -> list[EpisodicEvent]:
        """`project_id`'s last team's full episodic record, in order -- the same
        journal `cuttlefish show` reads, for the dashboard's own event-tail view."""
        project = self._projects.get(project_id)
        return self._last_team_events(project)
