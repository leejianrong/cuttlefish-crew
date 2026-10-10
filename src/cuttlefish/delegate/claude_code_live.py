"""Claude Code with its permission requests answered live (V4-K).

``claude -p`` (``cuttlefish.delegate.claude_code``) denies anything that would need approval by
itself. With ``--input-format stream-json --permission-prompt-tool stdio`` the same process
instead sends a ``control_request`` (``subtype: can_use_tool``) on stdout and waits for a
``control_response`` on stdin: the same shape as kopicode's ``consent.request``, so the same
:class:`~cuttlefish.delegate.consent.ConsentPolicy`, or :class:`~cuttlefish.requests.AskingDecider`
for a person, decides a ``Bash`` command. What the protocol looks like, and what was and was not
tried, is in ``docs/research/claude-code-stream-json-spike.md`` (``claude`` 2.1.296).

The rest of the stream is the one ``claude -p`` already writes, so the outcome comes from
:func:`~cuttlefish.delegate.claude_code.classify_stream`, told which calls this host refused.

Differences from the one-shot mapping: no ``Bash(<command>:*)`` allow patterns are passed. Those
let Claude Code approve a command on its own reading of the pattern; here every command that
prompts is decided by our stricter policy (a plain word list, nothing chained). What Claude Code
considers read-only (``ls``) never prompts, so it is not ours to refuse. The never-allowed
prefixes stay as ``--disallowedTools``, the backstop for anything that does not prompt.
``AskUserQuestion`` goes to a person when one can be asked. Every other tool that prompts is
refused, as it was with ``--permission-prompts none``.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import inspect
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from cuttlefish.agents.outcome import ConsentDecisionRecord, DelegationError, DelegationOutcome
from cuttlefish.delegate.claude_code import (
    _AUTO_EXTRA_DENIED_BASH_PREFIXES,
    _EDIT_TOOLS,
    _NEVER_ALLOWED_BASH_PREFIXES,
    ENV_PASSTHROUGH,
    classify_stream,
)
from cuttlefish.delegate.codex_app_server import inside
from cuttlefish.delegate.kopicode_serve import Decider
from cuttlefish.delegate.subprocess_env import merge_env

_LOG = logging.getLogger(__name__)

_INTERRUPT_GRACE = 15.0
_STDERR_CAP = 64 * 1024
_STDERR_TAIL_CHARS = 2000
_DETAIL_CHARS = 1024

#: Puts a model's question (and the options it gave) to a person: their answer, or ``None``.
QuestionHandler = Callable[[str, str], Awaitable[str | None]]


def build_live_argv(binary: str, *, mode: str = "standard") -> list[str]:
    """The argv for one live session. Edits inside the working directory are accepted by Claude
    Code itself (``acceptEdits``); every other prompt reaches the host."""
    args = [
        binary,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "acceptEdits",
        "--permission-prompts",
        "host",
        "--permission-prompt-tool",
        "stdio",
    ]
    denied = [f"Bash({prefix}:*)" for prefix in _NEVER_ALLOWED_BASH_PREFIXES]
    if mode == "auto":
        args.extend(["--allowedTools", "Bash"])
        denied.extend(f"Bash({prefix}:*)" for prefix in _AUTO_EXTRA_DENIED_BASH_PREFIXES)
    if mode == "read-only":
        denied.extend(_EDIT_TOOLS)
    args.append("--disallowedTools")
    args.extend(denied)
    return args


@dataclasses.dataclass
class _Session:
    root: str
    edits: bool
    events: list[Mapping[str, Any]] = dataclasses.field(default_factory=list)
    consents: list[ConsentDecisionRecord] = dataclasses.field(default_factory=list)
    denied_ids: set[str] = dataclasses.field(default_factory=set)
    result: asyncio.Future[None] | None = None

    def record(self, kind: str, detail: str, allowed: bool, rule: str) -> None:
        self.consents.append(
            ConsentDecisionRecord(
                kind, detail[:_DETAIL_CHARS], "allow" if allowed else "deny", rule
            )
        )


class _Connection:
    def __init__(
        self,
        process: asyncio.subprocess.Process,
        session: _Session,
        decide: Decider,
        ask: QuestionHandler | None,
    ) -> None:
        self._process = process
        self._session = session
        self._decide = decide
        self._ask = ask
        self._handlers: set[asyncio.Task[None]] = set()
        self._stderr = bytearray()
        self.done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        assert process.stdout is not None and process.stderr is not None
        self._reader = asyncio.create_task(self._read(process.stdout))
        self._stderr_reader = asyncio.create_task(self._drain(process.stderr))

    async def stderr_tail(self) -> str:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.shield(self._stderr_reader), 1.0)
        return self._stderr.decode("utf-8", errors="replace").strip()[-_STDERR_TAIL_CHARS:]

    async def _drain(self, stream: asyncio.StreamReader) -> None:
        while chunk := await stream.read(4096):
            if len(self._stderr) < _STDERR_CAP:
                self._stderr += chunk

    async def write(self, message: Mapping[str, Any]) -> None:
        assert self._process.stdin is not None
        self._process.stdin.write(json.dumps(message).encode() + b"\n")
        with contextlib.suppress(ConnectionError, BrokenPipeError):
            await self._process.stdin.drain()

    async def _read(self, stream: asyncio.StreamReader) -> None:
        try:
            while line := await stream.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    _LOG.debug("claude: non-JSON line %r", line[:200])
                    continue
                if isinstance(message, dict):
                    self._dispatch(message)
        finally:
            if not self.done.done():
                tail = await self.stderr_tail()
                self.done.set_exception(
                    DelegationError(
                        f"Claude Code exited before the turn finished; stderr: {tail or '<empty>'}"
                    )
                )

    def _dispatch(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "control_request":
            task = asyncio.create_task(self._answer(message))
            self._handlers.add(task)
            task.add_done_callback(self._handlers.discard)
            return
        if kind in ("control_response", "control_cancel_request", "rate_limit_event"):
            return
        self._session.events.append(message)
        if kind == "result" and not self.done.done():
            self.done.set_result(None)

    async def _answer(self, message: Mapping[str, Any]) -> None:
        """Reply to one request. Never raises: an unanswered request would hold the turn."""
        request = message.get("request")
        request = request if isinstance(request, Mapping) else {}
        try:
            if request.get("subtype") != "can_use_tool":
                response: Mapping[str, Any] = {
                    "subtype": "error",
                    "request_id": message.get("request_id"),
                    "error": f"unsupported: {request.get('subtype')}",
                }
            else:
                decision = await self._decide_tool(request)
                response = {
                    "subtype": "success",
                    "request_id": message.get("request_id"),
                    "response": decision,
                }
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.exception("claude: could not answer a request; denying it")
            response = {
                "subtype": "success",
                "request_id": message.get("request_id"),
                "response": {"behavior": "deny", "message": "cuttlefish could not decide this"},
            }
        await self.write({"type": "control_response", "response": response})

    async def _decide_tool(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        tool = str(request.get("tool_name") or "")
        tool_input = request.get("input")
        tool_input = tool_input if isinstance(tool_input, Mapping) else {}
        call_id = request.get("tool_use_id")
        if tool == "AskUserQuestion":
            answered = await self._answer_questions(tool_input)
            if answered is not None:
                return {"behavior": "allow", "updatedInput": {**tool_input, "answers": answered}}
            return self._deny(call_id, "No person is available to answer this question.")
        if tool == "Bash":
            command = tool_input.get("command")
            detail = f"/bin/sh -c {command}" if isinstance(command, str) and command else ""
            decision = await self._ask_policy(detail or "unrecognised command")
            if not decision.asked:
                self._session.record(
                    "run_shell",
                    detail or "unrecognised command",
                    decision.answer == "allow",
                    decision.rule,
                )
            if decision.answer == "allow":
                return {"behavior": "allow", "updatedInput": dict(tool_input)}
            return self._deny(
                call_id, f"Not allowed by this project's command rules ({decision.rule})."
            )
        if tool in _EDIT_TOOLS:
            raw = tool_input.get("file_path") or tool_input.get("notebook_path")
            path = str(raw) if isinstance(raw, str) else ""
            ok = (
                self._session.edits
                and bool(path)
                and inside(
                    self._session.root,
                    path if Path(path).is_absolute() else f"{self._session.root}/{path}",
                )
            )
            self._session.record(
                "file_change",
                path or "file change",
                ok,
                "in_root_edit" if ok else "edit_outside_root",
            )
            if ok:
                return {"behavior": "allow", "updatedInput": dict(tool_input)}
            return self._deny(call_id, "Writes outside the project folder are not allowed.")
        self._session.record("tool", tool, False, "tool_not_granted")
        return self._deny(call_id, f"{tool} is not allowed for this role.")

    async def _ask_policy(self, detail: str) -> Any:
        decided = self._decide("run_shell", detail)
        return await decided if inspect.isawaitable(decided) else decided

    async def _answer_questions(self, tool_input: Mapping[str, Any]) -> dict[str, str] | None:
        questions = tool_input.get("questions")
        if self._ask is None or not isinstance(questions, list) or not questions:
            return None
        answers: dict[str, str] = {}
        for item in questions:
            if not isinstance(item, Mapping) or not isinstance(item.get("question"), str):
                return None
            options = item.get("options")
            labels = (
                [str(o.get("label")) for o in options if isinstance(o, Mapping) and o.get("label")]
                if isinstance(options, list)
                else []
            )
            context = "Options: " + ", ".join(labels) if labels else ""
            text = await self._ask(str(item["question"]), context)
            if text is None:
                return None
            answers[str(item["question"])] = text
        return answers

    def _deny(self, call_id: object, message: str) -> Mapping[str, Any]:
        if isinstance(call_id, str):
            self._session.denied_ids.add(call_id)
        return {"behavior": "deny", "message": message}

    async def close(self) -> None:
        for task in list(self._handlers):
            task.cancel()
        if self._process.stdin is not None and not self._process.stdin.is_closing():
            self._process.stdin.close()
        try:
            await asyncio.wait_for(self._process.wait(), _INTERRUPT_GRACE)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                self._process.kill()
            await self._process.wait()
        await asyncio.gather(self._reader, self._stderr_reader, return_exceptions=True)


async def run_claude_code_live(
    *,
    binary: str,
    task_text: str,
    root: str,
    decide: Decider,
    mode: str = "standard",
    ask: QuestionHandler | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> DelegationOutcome:
    """Run one delegation as one ``claude`` process and one turn, and classify what it did.

    ``decide`` is asked about every ``Bash`` command that prompts as ``("run_shell", "/bin/sh -c
    <line>")``. A turn past ``timeout`` is interrupted (``control_request`` ``interrupt``) and
    reported as a ``round_timeout`` failure; a cancelled call interrupts it and re-raises.

    Raises :class:`DelegationError` for the binary missing or the process dying mid-turn.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *build_live_argv(binary, mode=mode),
            cwd=root,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merge_env(env, root=root, passthrough=ENV_PASSTHROUGH),
        )
    except FileNotFoundError as exc:
        raise DelegationError(f"Claude Code binary {binary!r} not found") from exc

    session = _Session(root, edits=mode != "read-only")
    connection = _Connection(process, session, decide, ask)
    timed_out = False
    try:
        await connection.write({"type": "user", "message": {"role": "user", "content": task_text}})
        try:
            await asyncio.wait_for(asyncio.shield(connection.done), timeout)
        except TimeoutError:
            timed_out = True
            await _interrupt(connection)
    except asyncio.CancelledError:
        await asyncio.shield(_interrupt(connection))
        raise
    except DelegationError as exc:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        raise DelegationError(str(exc)) from exc
    finally:
        await asyncio.shield(connection.close())

    if not any(e.get("type") == "result" for e in session.events):
        tail = await connection.stderr_tail()
        if not timed_out:
            raise DelegationError(
                f"Claude Code's stream ended with no result; stderr: {tail or '<empty>'}"
            )
        return DelegationOutcome(
            kind="failed",
            summary="Claude Code was stopped: the round ran past its time limit (round_timeout)",
            reason=f"stopped: the round ran longer than {timeout:g} seconds"
            if timeout is not None
            else "stopped: the round ran past its time limit",
            failure_kind="round_timeout",
            consent_decisions=session.consents,
        )
    outcome = classify_stream(session.events, root=root, denied_ids=frozenset(session.denied_ids))
    outcome = dataclasses.replace(outcome, consent_decisions=session.consents)
    if timed_out:
        limit = "its time limit" if timeout is None else f"{timeout:g} seconds"
        return dataclasses.replace(
            outcome,
            kind="failed",
            summary="Claude Code was stopped: the round ran past its time limit (round_timeout)",
            reason=f"stopped: the round ran longer than {limit}",
            failure_kind="round_timeout",
        )
    return outcome


async def _interrupt(connection: _Connection) -> None:
    """Ask Claude Code to end the turn and wait for its ``result``; best effort."""
    with contextlib.suppress(Exception):
        await connection.write(
            {
                "type": "control_request",
                "request_id": "cuttlefish-interrupt",
                "request": {"subtype": "interrupt"},
            }
        )
        await asyncio.wait_for(asyncio.shield(connection.done), _INTERRUPT_GRACE)
