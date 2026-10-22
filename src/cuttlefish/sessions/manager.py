"""Start, reattach to, or relaunch a project's agent in tmux (CUT-95).

One tmux session per project, named from its id, running the backend's own interactive tool
(``claude``, ``codex``, ``kopicode``) in the project root. :meth:`SessionManager.ensure` says
which of three things it did, and records it:

* ``reattached``: the tmux session was still there (the daemon restarted, the browser closed);
* ``relaunched``: the tmux session was gone (a reboot, a kill) and the tool was started with its
  own resume command, so the conversation carries on;
* ``started``: there was nothing to resume.

The record lives in ``<root>/.cuttlefish/agent-session.json`` next to the episodic store, so it
follows the project. It holds no secret. A project is only reconciled at daemon start when its
record says ``wanted``, which Stop clears, so stopping an agent on purpose is never undone.

A tmux-hosted agent asks for permission in its own terminal (the terminal view, CUT-96). A Claude
Code session launched with our hooks (ADR-0040) also raises a Needs you card for it, and the
terminal can still answer; the other backends ask in their terminals only.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shlex
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from cuttlefish.artifacts import AGENT_NOTE
from cuttlefish.sessions.hooks import permission_mode
from cuttlefish.sessions.tmux import Tmux, TmuxError, session_name, shell_session_name

Action = Literal["started", "reattached", "relaunched"]
Asks = Literal["needs-you", "auto", "terminal"]
State = Literal["running", "stopped", "none"]

BACKENDS = ("claude-code", "codex", "kopicode")


class SessionError(RuntimeError):
    """The session could not be started or stopped."""


#: A unix socket path may not be longer than about 107 bytes; leave room for tmux's own suffix.
_MAX_SOCKET_PATH = 90


def default_socket() -> Path:
    """``~/.cuttlefish/tmux.sock``: under ``HOME`` so a scratch ``HOME`` has its own server.

    A deep ``HOME`` (a scratch directory, a long mount) would overflow the socket path limit, so
    then it is a short, owner-only path under ``/tmp`` named from a hash of the wanted one: still
    one server per ``HOME``.
    """
    wanted_path = Path.home() / ".cuttlefish" / "tmux.sock"
    if len(str(wanted_path)) <= _MAX_SOCKET_PATH:
        return wanted_path
    digest = hashlib.sha1(str(wanted_path).encode(), usedforsecurity=False).hexdigest()[:12]
    folder = Path("/tmp") / f"cuttlefish-{os.getuid()}"
    folder.mkdir(mode=0o700, exist_ok=True)
    return folder / f"{digest}.sock"


@dataclasses.dataclass(frozen=True)
class SessionInfo:
    state: State
    tmux_session: str
    backend: str | None = None
    session_id: str | None = None
    last_action: Action | None = None
    at: str | None = None
    #: Where this session's permission questions go: ``needs-you`` (a Claude Code session with our
    #: hooks, ADR-0040), ``auto`` (it never asks) or ``terminal`` (its own terminal, the default).
    asks: Asks = "terminal"

    def to_json(self) -> dict[str, object]:
        return dataclasses.asdict(self)


def _record_path(root: str) -> Path:
    return Path(root) / ".cuttlefish" / "agent-session.json"


def _read(root: str) -> dict[str, object]:
    try:
        data = json.loads(_record_path(root).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(root: str, record: Mapping[str, object]) -> None:
    path = _record_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_suffix(".tmp")
    scratch.write_text(json.dumps(record, indent=2, sort_keys=True))
    scratch.replace(path)


def _goal_path(root: str) -> Path:
    return Path(root) / ".cuttlefish" / "goal.json"


def read_goal(root: str) -> dict[str, str] | None:
    """The goal last given to the project's builder (CUT-171): ``text``, ``at`` and ``backend``."""
    try:
        data = json.loads(_goal_path(root).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        return None
    return {k: v for k, v in data.items() if isinstance(v, str)}


def write_goal(root: str, text: str, backend: str) -> dict[str, str]:
    goal = {"text": text, "at": _now(), "backend": backend}
    path = _goal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_suffix(".tmp")
    scratch.write_text(json.dumps(goal, indent=2, sort_keys=True))
    scratch.replace(path)
    return goal


def wanted(root: str) -> bool:
    return _read(root).get("wanted") is True


def _latest_kopicode_session(binary: str, root: str) -> str | None:
    """The newest session id ``kopicode sessions -json`` lists for this root, if any."""
    try:
        result = subprocess.run(
            [binary, "sessions", "-json"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    best: tuple[str, str] | None = None
    for line in result.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            started = str(row.get("started_at") or "")
            if best is None or started > best[0]:
                best = (started, row["id"])
    return best[1] if best else None


def _first_prompt(text: str) -> str:
    """``text`` as one argument that cannot be read as a flag: the tools take their first message
    as a trailing positional, so a goal that begins with ``-`` is given a lead-in."""
    return f"Goal: {text}" if text.startswith("-") else text


def launch_argv(
    backend: str,
    *,
    root: str,
    session_id: str,
    resume: bool,
    kopicode_binary: str = "kopicode",
    claude_args: Sequence[str] = (),
    prompt: str | None = None,
) -> list[str]:
    """The command that starts the backend's interactive tool, resuming when asked.

    Where a resume can fail because there is no conversation to resume (the agent was killed
    before its first prompt), the shell form falls back to a fresh start in the same command.

    ``prompt`` is the first message (a goal, CUT-171): both claude and codex take it as a trailing
    argument, so nothing is typed into a terminal that may be showing a dialog. kopicode's REPL
    has no such argument, so a prompt for it is refused rather than dropped.
    """
    first = _first_prompt(prompt) if prompt else None
    tail = f" {shlex.quote(first)}" if first else ""
    if backend == "claude-code":
        sid = shlex.quote(session_id)
        # `claude_args`: our hooks and permission mode (ADR-0040), for a resume as for a start.
        note = (
            f"--append-system-prompt {shlex.quote(AGENT_NOTE)} {shlex.join(claude_args)}".rstrip()
            + tail
        )
        if resume:
            return [
                "sh",
                "-c",
                f"claude --resume {sid} {note} || exec claude --session-id {sid} {note}",
            ]
        return [
            "claude",
            "--session-id",
            session_id,
            "--append-system-prompt",
            AGENT_NOTE,
            *claude_args,
            *([first] if first else []),
        ]
    if backend == "codex":
        if resume:
            return ["sh", "-c", f"codex resume --last{tail} || exec codex{tail}"]
        return ["codex", *([first] if first else [])]
    if backend == "kopicode":
        if first:
            raise SessionError(
                "kopicode's interactive session does not take a first message yet; "
                "start the session and type the goal in the Terminal"
            )
        previous = _latest_kopicode_session(kopicode_binary, root) if resume else None
        return [kopicode_binary, "-resume", previous] if previous else [kopicode_binary]
    raise SessionError(f"no interactive session for backend {backend!r}")


_STAMP = re.compile(r"\+00:00$")


def _now() -> str:
    return _STAMP.sub("Z", datetime.now(UTC).isoformat(timespec="seconds"))


class SessionManager:
    def __init__(
        self,
        tmux: Tmux,
        *,
        env_for: Callable[[str, str], Mapping[str, str]],
        kopicode_binary: str = "kopicode",
    ) -> None:
        self._tmux = tmux
        #: Builds the agent's environment for a root (``merge_env``), so it is never inherited.
        self._env_for = env_for
        self._kopicode = kopicode_binary

    def status(self, project_id: str, root: str) -> SessionInfo:
        name = session_name(project_id)
        record = _read(root)
        try:
            alive = self._tmux.has_session(name)
        except TmuxError:
            alive = False
        state: State = "running" if alive else "stopped" if record else "none"
        action = record.get("last_action")
        return SessionInfo(
            state=state,
            tmux_session=name,
            backend=_str(record.get("backend")),
            session_id=_str(record.get("session_id")),
            last_action=_action(action),
            at=_str(record.get("at")),
            asks=_asks(record.get("asks")),
        )

    def ensure(
        self,
        project_id: str,
        root: str,
        backend: str,
        *,
        mode: str = "standard",
        hooks_settings: Path | None = None,
        prompt: str | None = None,
    ) -> SessionInfo:
        """Make sure the project's agent is running in tmux, and record which way it got there.

        ``hooks_settings`` (Claude Code only, ADR-0040) is the settings file that gives the session
        its permission hook, launched with the permission mode ``mode`` maps to. A session that is
        found already running keeps the way it was launched.

        ``prompt`` is a first message for a tool that is being launched now. A session that is
        already running cannot take it without typing into its terminal, so that is refused."""
        if backend not in BACKENDS:
            raise SessionError(f"backend {backend!r} has no interactive session")
        name = session_name(project_id)
        record = dict(_read(root))
        try:
            alive = self._tmux.has_session(name)
        except TmuxError as exc:
            raise SessionError(str(exc)) from exc
        action: Action
        if alive and prompt:
            raise SessionError(
                "the builder is already running, so it has a conversation to type to"
            )
        if alive:
            action = "reattached"
        else:
            previous = record.get("backend") == backend and record.get("launched") is True
            session_id = _str(record.get("session_id")) if previous else None
            session_id = session_id or str(uuid.uuid4())
            hooked = backend == "claude-code" and hooks_settings is not None
            argv = launch_argv(
                backend,
                root=root,
                session_id=session_id,
                resume=previous,
                kopicode_binary=self._kopicode,
                prompt=prompt,
                claude_args=(
                    ["--settings", str(hooks_settings), "--permission-mode", permission_mode(mode)]
                    if hooked
                    else []
                ),
            )
            try:
                self._tmux.new_session(name, cwd=root, argv=argv, env=self._env_for(root, backend))
            except TmuxError as exc:
                raise SessionError(str(exc)) from exc
            action = "relaunched" if previous else "started"
            record.update(session_id=session_id, launched=True)
            record["asks"] = ("auto" if mode == "auto" else "needs-you") if hooked else "terminal"
        record.update(backend=backend, wanted=True, last_action=action, at=_now())
        _write(root, record)
        return self.status(project_id, root)

    def ensure_shell(self, project_id: str, root: str) -> str:
        """Make sure the project's plain shell is running in tmux and return its session name
        (CUT-170). It is the person's own shell in the project folder, needs no agent, and is
        not recorded or reconciled: when it exits, the next view starts a fresh one. Its
        environment is the agent's filtered one and carries no project secrets."""
        name = shell_session_name(project_id)
        try:
            if not self._tmux.has_session(name):
                env = dict(self._env_for(root, "shell"))
                shell = env.get("SHELL") or "/bin/bash"
                self._tmux.new_session(name, cwd=root, argv=[shell], env=env)
        except TmuxError as exc:
            raise SessionError(str(exc)) from exc
        return name

    def stop_shell(self, project_id: str) -> None:
        self._tmux.kill_session(shell_session_name(project_id))

    def tmux_session(self, project_id: str) -> tuple[Tmux, str]:
        """The tmux server and session name a terminal view attaches to."""
        return self._tmux, session_name(project_id)

    def stop(self, project_id: str, root: str) -> SessionInfo:
        """End the tmux session and stop reconciling it. The conversation stays on disk."""
        self._tmux.kill_session(session_name(project_id))
        record = dict(_read(root))
        if record:
            record["wanted"] = False
            _write(root, record)
        return self.status(project_id, root)


def _asks(value: object) -> Asks:
    return value if value == "needs-you" or value == "auto" else "terminal"


def _action(value: object) -> Action | None:
    if value == "started" or value == "reattached" or value == "relaunched":
        return value
    return None


def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None


#: What each tool may be given from the daemon's own environment, on top of the allowlist. The
#: interactive tools mostly use the person's own login under ``HOME``; an API key is only passed
#: when one is set in the daemon's environment.
_BACKEND_ENV = {
    "claude-code": ("ANTHROPIC_API_KEY",),
    "codex": ("OPENAI_API_KEY",),
    "kopicode": ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"),
}


def agent_env(root: str, backend: str) -> dict[str, str]:
    """The allowlisted environment for an interactive agent in ``root`` (ADR-0029). Imported late
    so this module stays importable without the delegation package."""
    from cuttlefish.delegate.subprocess_env import merge_env

    env = merge_env({}, root=root, passthrough=_BACKEND_ENV.get(backend, ()))
    env.setdefault("TERM", "xterm-256color")
    return env
