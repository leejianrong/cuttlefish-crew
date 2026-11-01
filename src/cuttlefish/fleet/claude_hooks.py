"""What the daemon does with the hooks of a project's Claude Code session (ADR-0040).

The hook script (``sessions/hook_script.py``) posts each hook's JSON here and prints the answer. A
``PermissionRequest`` is decided the way a stream-json request is (ADR-0028): the project's own
:class:`~cuttlefish.delegate.consent.ConsentPolicy` for a shell command, a folder check for an edit,
and a Needs you card, held until a person answers, for anything left. The card is journaled
under the project's agent session (``session:<project id>``), not a team.

Failure never approves. An answer is ``{"decision": "allow" | "deny"}`` only when it was decided
here, and ``{}`` (no decision, so the terminal asks) for anything else: a question for the
terminal, an expired or abandoned card, a request the terminal answered first."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cuttlefish.delegate.codex_app_server import inside
from cuttlefish.delegate.consent import ConsentPolicy, command_line
from cuttlefish.delegate.never_allowed import repo_default_branches
from cuttlefish.delegate.presets import resolve_allow
from cuttlefish.projects.store import Project
from cuttlefish.requests import WHY, UnknownRequestError
from cuttlefish.sessions.manager import note_session_id

if TYPE_CHECKING:
    from cuttlefish.fleet.daemon import FleetDaemon

_LOG = logging.getLogger("cuttlefish.fleet.claude_hooks")

EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
#: Cards waiting at once for one project's session. More are left to the terminal.
MAX_PENDING = 20
#: How many recent tool calls are remembered to find a call's id (for a denial in the terminal).
_REMEMBERED_CALLS = 64
#: How long the transcript is read for a call the terminal rejected.
_TAIL_EVERY_S = 1.0
_DETAIL_CHARS = 1024
_REJECTED = "doesn't want to proceed"
#: A turn with no hook for this long is shown as unknown, not busy: Esc in the terminal ends a turn
#: without a `Stop`, so silence is the only sign of it.
_BUSY_SILENCE_S = 300.0

Gone = Callable[[], Awaitable[None]]


def session_scope(project_id: str) -> str:
    """The journal scope of a project's agent session: its requests live here, not in a team."""
    return f"session:{project_id}"


def input_key(tool: str, tool_input: Mapping[str, Any]) -> str:
    """How a tool call is recognised across its hooks: the tool and its input, which the script
    clips the same way each time."""
    return tool + "\0" + json.dumps(tool_input, sort_keys=True, default=str)[:4000]


def summarise(tool: str, tool_input: Mapping[str, Any]) -> str:
    """What a card shows for a tool that is not a shell command: its name and its first inputs."""
    parts = [f"{key}: {value}" for key, value in list(tool_input.items())[:4]]
    return f"{tool}\n" + "\n".join(parts) if parts else tool


def _rejected(line: bytes, call_id: str | None) -> bool:
    """Whether a transcript line says the person refused this call in the terminal."""
    try:
        row = json.loads(line)
    except ValueError:
        return False
    content = row.get("message", {}).get("content") if isinstance(row, dict) else None
    if not isinstance(content, list):
        return False
    for item in content:
        if (
            not isinstance(item, dict)
            or item.get("type") != "tool_result"
            or not item.get("is_error")
        ):
            continue
        if call_id is not None and item.get("tool_use_id") != call_id:
            continue
        if _REJECTED in str(item.get("content", "")).lower():
            return True
    return False


class ClaudeHooks:
    def __init__(self, daemon: FleetDaemon) -> None:
        self._daemon = daemon
        #: project id -> recent calls (tool + input -> tool_use_id), from `PreToolUse`.
        self._calls: dict[str, collections.OrderedDict[str, str]] = {}
        #: project id -> request id -> (tool_use_id, input key) of the call it holds.
        self._held: dict[str, dict[str, tuple[str | None, str]]] = {}
        #: project id -> the transcript path the session's own hooks last reported (CUT-172). It
        #: follows the session when Claude Code starts a new one (`/clear`), which a path worked
        #: out from the recorded session id would not.
        self._transcripts: dict[str, str] = {}
        #: project id -> what the session's hooks last said it was doing, and when (monotonic).
        self._activity: dict[str, tuple[str, float]] = {}
        #: Projects whose terminal is showing a dialog of its own: a permission the daemon left to
        #: it. Cleared by the next hook that shows the session moved on.
        self._terminal_dialog: set[str] = set()

    def transcript_path(self, project_id: str) -> str | None:
        return self._transcripts.get(project_id)

    def holds(self, project_id: str) -> bool:
        """Whether a permission card of this session is waiting for an answer."""
        return bool(self._held.get(project_id))

    def activity(self, project_id: str) -> tuple[str, float | None]:
        """What the session is doing, from its hooks (CUT-173): ``dialog`` (a permission is waiting,
        here or in the terminal), ``busy``, ``idle``, or ``unknown`` (no hook seen yet, or a turn
        that has been silent so long a lost ``Stop`` is likelier than a long tool). The second
        value is how many seconds ago that was said."""
        if self._held.get(project_id) or project_id in self._terminal_dialog:
            return "dialog", None
        seen = self._activity.get(project_id)
        if seen is None:
            return "unknown", None
        state, at = seen
        age = time.monotonic() - at
        if state == "busy" and age > _BUSY_SILENCE_S:
            return "unknown", age
        return state, age

    # -- entry -------------------------------------------------------------------------------

    async def handle(
        self, project: Project, payload: Mapping[str, Any], gone: Gone
    ) -> dict[str, Any]:
        event = payload.get("hook_event_name")
        reported = _str(payload.get("transcript_path"))
        if reported:
            self._transcripts[project.id] = reported
        sid = _str(payload.get("session_id"))
        if sid:
            await asyncio.to_thread(note_session_id, project.root, sid)
        if event in ("UserPromptSubmit", "PreToolUse", "PostToolUse"):
            self._terminal_dialog.discard(project.id)
            self._activity[project.id] = ("busy", time.monotonic())
            self._daemon.autopilot.on_activity(project)  # the turn is not over
        elif event == "Stop":
            self._terminal_dialog.discard(project.id)
            self._activity[project.id] = ("idle", time.monotonic())
            self._daemon.autopilot.on_stop(project)
        tool = str(payload.get("tool_name") or "")
        raw = payload.get("tool_input")
        tool_input: Mapping[str, Any] = raw if isinstance(raw, dict) else {}
        if event == "PreToolUse":
            self._remember(project.id, tool, tool_input, payload.get("tool_use_id"))
        elif event == "PostToolUse":
            # Answered in the terminal and allowed there: the card has nothing left to ask.
            self._supersede(
                project.id,
                call_id=_str(payload.get("tool_use_id")),
                key=input_key(tool, tool_input),
            )
        elif event in ("UserPromptSubmit", "Stop"):
            self._supersede(project.id)
        elif event == "PermissionRequest":
            answer = await self._permission(project, payload, tool, tool_input, gone)
            if not answer and project.mode != "auto":
                self._terminal_dialog.add(project.id)  # the terminal's own dialog asks
            return answer
        return {}

    # -- the decision ------------------------------------------------------------------------

    async def _permission(
        self,
        project: Project,
        payload: Mapping[str, Any],
        tool: str,
        tool_input: Mapping[str, Any],
        gone: Gone,
    ) -> dict[str, Any]:
        mode = project.mode
        if mode == "auto" or not tool or tool == "AskUserQuestion":
            return {}  # Auto never asks; a question belongs to the terminal (ADR-0040)
        if tool in EDIT_TOOLS:
            return self._decide_edit(project, tool_input)
        broker = self._daemon.requests
        scope = session_scope(project.id)
        if len(self._held.get(project.id, {})) >= MAX_PENDING:
            return {}
        suggestion: tuple[str, ...] | None = ()
        title: str | None = None
        if tool == "Bash":
            command = tool_input.get("command")
            if not isinstance(command, str) or not command.strip():
                return {}
            line = f"/bin/sh -c {command}"
            allow = [] if mode == "ask-first" else resolve_allow(project.allow, project.presets)
            policy = ConsentPolicy(
                [*allow, *broker.grants(scope)],
                default_branches=repo_default_branches(Path(project.root)),
            )
            decision = policy.decide("run_shell", line)
            if decision.answer == "allow":
                return {"decision": "allow"}
            if not decision.askable or command_line(line) is None:
                return {
                    "decision": "deny",
                    "message": f"Not allowed by this project's command rules ({decision.rule}).",
                }
            shown = command_line(line) or command
            why = WHY.get(decision.rule, decision.rule)
            suggestion = policy.suggest_rule(shown)
            title = "Claude Code wants to run a command that isn't on the list"
        else:
            shown = summarise(tool, tool_input)
            why = f"Claude Code asks before using {tool}."
            title = f"Claude Code wants to use {tool}"
        self._daemon.open_session_journal(project)
        if broker.is_closed(scope):
            return {}  # the person stopped the session and it has not been started again
        request = broker.raise_permission(
            project_id=project.id,
            team_id=scope,
            role=None,
            backend="claude-code",
            line=shown[:_DETAIL_CHARS],
            why=why,
            window_s=self._daemon.request_window_s,
            suggestion=suggestion,
            title=title,
            session=True,
        )
        key = input_key(tool, tool_input)
        call_id = self._calls[project.id].get(key) if project.id in self._calls else None
        self._held.setdefault(project.id, {})[request.id] = (call_id, key)
        hold = asyncio.ensure_future(broker.hold(request))
        away = asyncio.ensure_future(gone())
        tail = asyncio.ensure_future(
            self._tail(request.id, _str(payload.get("transcript_path")), call_id)
        )
        try:
            await asyncio.wait({hold, away}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # Whether the hook process went away (its own timeout, a killed session) or this
            # handler was cancelled, nobody is waiting any more: a card still pending ends here.
            if not hold.done():
                with contextlib.suppress(UnknownRequestError):
                    broker.resolve_system(request.id, "abandoned", by="system")
            self._held.get(project.id, {}).pop(request.id, None)
            for task in (away, tail):
                task.cancel()
            await asyncio.gather(away, tail, hold, return_exceptions=True)
        outcome = hold.result() if hold.done() and not hold.cancelled() else None
        if outcome is None:
            return {}
        if outcome.allows:
            return {"decision": "allow"}
        if outcome.resolution == "denied":
            return {"decision": "deny", "message": "Denied from Needs you in cuttlefish."}
        return {}

    def _decide_edit(self, project: Project, tool_input: Mapping[str, Any]) -> dict[str, Any]:
        raw = tool_input.get("file_path") or tool_input.get("notebook_path")
        path = raw if isinstance(raw, str) else ""
        absolute = path if Path(path).is_absolute() else f"{project.root}/{path}"
        if path and inside(project.root, absolute):
            return {"decision": "allow"}
        return {"decision": "deny", "message": "Writes outside the project folder are not allowed."}

    # -- clearing a card that the terminal answered ----------------------------------------------

    def _remember(
        self, project_id: str, tool: str, tool_input: Mapping[str, Any], call_id: Any
    ) -> None:
        if not isinstance(call_id, str):
            return
        calls = self._calls.setdefault(project_id, collections.OrderedDict())
        calls[input_key(tool, tool_input)] = call_id
        while len(calls) > _REMEMBERED_CALLS:
            calls.popitem(last=False)

    def _supersede(
        self, project_id: str, *, call_id: str | None = None, key: str | None = None
    ) -> None:
        """End the cards a person answered in the terminal, or all of a session's cards."""
        for request_id, (held_id, held_key) in list(self._held.get(project_id, {}).items()):
            if call_id is not None and held_id is not None:
                if held_id != call_id:
                    continue
            elif key is not None and held_key != key:
                continue
            with contextlib.suppress(UnknownRequestError):
                self._daemon.requests.resolve_system(request_id, "superseded", by="person")

    async def _tail(self, request_id: str, transcript: str | None, call_id: str | None) -> None:
        """A call denied in the terminal fires no hook; the session transcript is the one record.
        Read what is added to it while the card waits, and end the card when it shows a refusal."""
        if not transcript:
            return
        try:
            offset = Path(transcript).stat().st_size
        except OSError:
            return
        pending = b""
        while True:
            await asyncio.sleep(_TAIL_EVERY_S)
            try:
                with Path(transcript).open("rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
            except OSError:
                continue
            offset += len(chunk)
            *lines, pending = (pending + chunk).split(b"\n")
            if any(_rejected(line, call_id) for line in lines):
                with contextlib.suppress(UnknownRequestError):
                    self._daemon.requests.resolve_system(request_id, "superseded", by="person")
                return

    def release_all(self) -> None:
        """The daemon is shutting down: every held hook is told there is no decision, so its script
        exits and the terminal's own dialog stays. Without this a held hook keeps the server's
        graceful shutdown waiting for as long as the request window."""
        for project_id in list(self._held):
            self._daemon.requests.end_team(
                session_scope(project_id), "abandoned", keep_decisions=True
            )

    def end_session(self, project_id: str) -> None:
        """The person stopped the agent session: its cards end and its hooks go quiet."""
        self._daemon.requests.end_team(session_scope(project_id), "cancelled")
        self._calls.pop(project_id, None)
        self._activity.pop(project_id, None)
        self._terminal_dialog.discard(project_id)


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


__all__ = ["ClaudeHooks", "session_scope"]
