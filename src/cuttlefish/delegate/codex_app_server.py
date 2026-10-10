"""Codex driven through ``codex app-server``, so a command can wait for a person (V4-M).

``codex exec`` (``cuttlefish.delegate.codex``) is one-shot: it cannot pause for an answer, and its
only dial is a three-tier sandbox. ``app-server`` speaks newline-delimited JSON-RPC over stdio. Run
under ``approvalPolicy: "untrusted"`` it sends a server request, ``item/commandExecution/
requestApproval``, for every command, carrying the exact command line and cwd; the reply is a plain
JSON-RPC response. That is the same shape as kopicode's ``consent.request``, so the same
:class:`~cuttlefish.delegate.consent.ConsentPolicy` (and, for a person's answer,
:class:`~cuttlefish.requests.AskingDecider`) decides it. What the protocol looks like, and what was
and was not tried, is in ``docs/research/codex-app-server-spike.md``.

**An accepted command runs outside Codex's sandbox** (verified in the spike: an accepted
``touch /tmp/x`` created the file). The sandbox is no longer a backstop once we say yes, so the
never-allowed list, the project's allowed commands and the working-directory check here carry all
the weight, and the existing gap (a script or path built at run time can still write elsewhere)
applies.

Version: written against ``codex-cli`` 0.155.1 to 0.161.0. The protocol is marked experimental by
Codex; a handshake or a method it no longer has fails the round with the server's own message.

A file edit is asked about too (``item/fileChange/requestApproval``, verified live: the spike's
in-root ``apply_patch`` ran unasked, but under ``untrusted`` it does not). It names no paths, so
they are read from the item's own ``item/started``; an edit with every path in the root is accepted.

Out of scope here: ``item/tool/requestUserInput`` (behind a feature flag Codex lists as under
development, so it is never enabled and never arrives), a person steering mid-round
(``turn/steer``), and a file edit that asks for a wider grant.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import inspect
import json
import logging
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from cuttlefish.agents.outcome import (
    ConsentDecisionRecord,
    DelegationError,
    DelegationOutcome,
    ToolCallRecord,
    describe_refusals,
)
from cuttlefish.delegate.consent import ConsentDecision
from cuttlefish.delegate.kopicode_serve import Decider
from cuttlefish.delegate.subprocess_env import merge_env

_LOG = logging.getLogger(__name__)

#: How long ``turn/interrupt`` and the process's own exit may take before it is killed.
_INTERRUPT_GRACE = 15.0
_STDERR_CAP = 64 * 1024
_STDERR_TAIL_CHARS = 2000
#: Longest command line copied into a journaled consent record.
_DETAIL_CHARS = 1024
#: The most of Codex's final answer that goes into an outcome's summary.
_REPLY_CHARS = 600

_SHELLS = frozenset({"sh", "bash", "zsh", "dash"})
_SHELL_FLAGS = frozenset({"-c", "-lc"})

Sandbox = Literal["read-only", "workspace-write"]


def shell_line(command: str) -> str | None:
    """The line inside ``/bin/bash -lc '<line>'`` (what Codex reports a command as), or ``None``
    when the command is not exactly a shell, ``-c`` or ``-lc``, and one argument."""
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if len(argv) == 3 and Path(argv[0]).name in _SHELLS and argv[1] in _SHELL_FLAGS:
        return argv[2]
    return None


def consent_detail(command: str) -> str:
    """``command`` as the ``detail`` :class:`ConsentPolicy` reads: ``/bin/sh -c <line>`` for a shell
    line. Anything else is marked so the policy denies it rather than reading it as a line."""
    line = shell_line(command)
    return f"/bin/sh -c {line}" if line is not None else f"unrecognised command: {command}"


def inside(root: str, cwd: str) -> bool:
    try:
        Path(cwd).resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return False
    return True


@dataclasses.dataclass
class _Round:
    """What one turn produced, gathered from its notifications."""

    root: str
    edited_paths: list[str] = dataclasses.field(default_factory=list)
    tool_calls: list[ToolCallRecord] = dataclasses.field(default_factory=list)
    consents: list[ConsentDecisionRecord] = dataclasses.field(default_factory=list)
    declined: int = 0
    tokens: int | None = None
    final_answer: str = ""
    status: str | None = None
    error: str | None = None

    def item_completed(self, item: Mapping[str, Any]) -> None:
        kind = item.get("type")
        if kind == "commandExecution":
            command = item.get("command")
            status = item.get("status")
            self.tool_calls.append(
                ToolCallRecord(
                    tool="command_execution",
                    detail=command if isinstance(command, str) else "",
                    status=(
                        "denied"
                        if status == "declined"
                        else "ok"
                        if status == "completed" and item.get("exitCode") in (0, None)
                        else "error"
                    ),
                )
            )
        elif kind == "fileChange":
            if item.get("status") == "declined":
                return
            changes = item.get("changes")
            detail: list[str] = []
            for change in changes if isinstance(changes, list) else []:
                path = change.get("path") if isinstance(change, Mapping) else None
                if not isinstance(path, str) or not path:
                    continue
                relative = _relativize(path, self.root)
                if relative not in self.edited_paths:
                    self.edited_paths.append(relative)
                kind_ = change.get("kind")
                detail.append(
                    f"{kind_.get('type') if isinstance(kind_, Mapping) else kind_} {path}"
                )
            self.tool_calls.append(
                ToolCallRecord(tool="file_change", detail="; ".join(detail), status="ok")
            )
        elif kind == "agentMessage" and item.get("phase") == "final_answer":
            text = item.get("text")
            self.final_answer = text if isinstance(text, str) else ""

    def usage(self, params: Mapping[str, Any]) -> None:
        usage = params.get("tokenUsage")
        total = usage.get("total") if isinstance(usage, Mapping) else None
        if isinstance(total, Mapping):
            self.tokens = int(total.get("inputTokens") or 0) + int(total.get("outputTokens") or 0)

    def _said(self, summary: str) -> str:
        """``summary`` followed by what Codex said last (its final answer), so a person or an
        MCP client reads what happened and not only how many files changed."""
        reply = " ".join(self.final_answer.split())[:_REPLY_CHARS]
        return f"{summary}: {reply}" if reply else summary

    def outcome(self) -> DelegationOutcome:
        common: dict[str, Any] = {
            "tokens": self.tokens,
            "tool_calls": self.tool_calls,
            "consent_decisions": self.consents,
        }
        if self.status == "completed":
            if self.edited_paths:
                return DelegationOutcome(
                    kind="completed",
                    summary=self._said(f"Codex edited {len(self.edited_paths)} file(s)"),
                    edited_paths=self.edited_paths,
                    **common,
                )
            if self.declined and not any(c.status == "ok" for c in self.tool_calls):
                return DelegationOutcome(
                    kind="refused",
                    summary=self._said("Codex's commands were declined and no file changed"),
                    reason=describe_refusals(self.consents)
                    or f"{self.declined} command(s) were not allowed",
                    **common,
                )
            return DelegationOutcome(
                kind="completed", summary=self._said("Codex finished with no edit needed"), **common
            )
        return DelegationOutcome(
            kind="failed",
            summary="Codex did not finish cleanly",
            reason=self.error or f"the turn ended {self.status or 'without a status'}",
            edited_paths=self.edited_paths,
            failure_kind="cancelled" if self.status == "interrupted" else None,
            **common,
        )


def _change_paths(item: Mapping[str, Any]) -> list[str]:
    changes = item.get("changes")
    if not isinstance(changes, list):
        return []
    return [
        path
        for change in changes
        if isinstance(change, Mapping) and isinstance(path := change.get("path"), str) and path
    ]


def _relativize(path: str, root: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return path


class _Connection:
    """One ``codex app-server`` process: requests out, notifications and server requests in."""

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        round_: _Round,
        decide: Decider,
        *,
        edits: bool,
    ) -> None:
        self._process = process
        self._round = round_
        self._decide = decide
        self._edits = edits
        #: A file-change approval names no paths, only the item; the paths came with its start.
        self._file_items: dict[str, list[str]] = {}
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[Mapping[str, Any]]] = {}
        self._handlers: set[asyncio.Task[None]] = set()
        self.turn_done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._stderr = bytearray()
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

    def _write(self, message: Mapping[str, Any]) -> None:
        assert self._process.stdin is not None
        self._process.stdin.write(json.dumps(message).encode() + b"\n")

    async def request(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        self._next_id += 1
        future: asyncio.Future[Mapping[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[self._next_id] = future
        self._write({"id": self._next_id, "method": method, "params": params})
        try:
            await self._process.stdin.drain()  # type: ignore[union-attr]
        except (ConnectionError, BrokenPipeError) as exc:
            raise DelegationError(f"Codex app-server closed its input during {method}") from exc
        return await future

    def notify(self, method: str) -> None:
        self._write({"method": method, "params": {}})

    async def _read(self, stream: asyncio.StreamReader) -> None:
        try:
            while line := await stream.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    _LOG.debug("codex app-server: non-JSON line %r", line[:200])
                    continue
                if isinstance(message, dict):
                    self._dispatch(message)
        finally:
            tail = await self.stderr_tail()
            error = DelegationError(
                f"Codex app-server exited before the turn finished; stderr: {tail or '<empty>'}"
            )
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)
            if not self.turn_done.done():
                self.turn_done.set_exception(error)

    def _dispatch(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method is None:
            future = self._pending.pop(message.get("id"), None)  # type: ignore[arg-type]
            if future is None or future.done():
                return
            error = message.get("error")
            if isinstance(error, Mapping):
                future.set_exception(
                    DelegationError(
                        f"Codex app-server error {error.get('code')}: {error.get('message')}"
                    )
                )
            else:
                result = message.get("result")
                future.set_result(result if isinstance(result, Mapping) else {})
            return
        params = message.get("params")
        params = params if isinstance(params, Mapping) else {}
        if "id" in message:
            task = asyncio.create_task(self._answer(message["id"], str(method), params))
            self._handlers.add(task)
            task.add_done_callback(self._handlers.discard)
        elif method == "item/started":
            item = params.get("item")
            if isinstance(item, Mapping) and item.get("type") == "fileChange":
                self._file_items[str(item.get("id"))] = _change_paths(item)
        elif method == "item/completed":
            item = params.get("item")
            if isinstance(item, Mapping):
                self._round.item_completed(item)
        elif method == "thread/tokenUsage/updated":
            self._round.usage(params)
        elif method == "turn/completed":
            turn = params.get("turn")
            turn = turn if isinstance(turn, Mapping) else {}
            self._round.status = str(turn.get("status") or "")
            error = turn.get("error")
            if isinstance(error, Mapping) and error.get("message"):
                self._round.error = str(error["message"])
            if not self.turn_done.done():
                self.turn_done.set_result(None)

    async def _answer(self, request_id: object, method: str, params: Mapping[str, Any]) -> None:
        """Reply to one server request. Never raises: an unanswered request would hold the turn."""
        try:
            result = await self._result_for(method, params)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.exception("codex app-server: could not answer %s; declining it", method)
            result = _DECLINES.get(method, {"decision": "decline"})
        if result is None:
            self._write(
                {"id": request_id, "error": {"code": -32601, "message": f"unsupported: {method}"}}
            )
        else:
            self._write({"id": request_id, "result": result})
        with contextlib.suppress(ConnectionError, BrokenPipeError):
            await self._process.stdin.drain()  # type: ignore[union-attr]

    async def _result_for(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any] | None:
        if method == "item/commandExecution/requestApproval":
            return {"decision": await self._decide_command(params)}
        if method == "item/fileChange/requestApproval":
            return {"decision": self._decide_file_change(params)}
        if method in _DECLINES:
            return _DECLINES[method]
        return None

    def _decide_file_change(self, params: Mapping[str, Any]) -> str:
        """``untrusted`` asks about every edit too. One whose every path is inside the root, with
        no wider grant asked for, is the work itself; anything else is a write outside the root,
        which is never allowed. Nothing is accepted in a read-only sandbox."""
        paths = self._file_items.get(str(params.get("itemId")), [])
        grant = params.get("grantRoot")
        root = self._round.root
        ok = (
            self._edits
            and bool(paths)
            and all(
                inside(root, path if Path(path).is_absolute() else f"{root}/{path}")
                for path in paths
            )
            and (not isinstance(grant, str) or inside(root, grant))
        )
        rule = "in_root_edit" if ok else "edit_outside_root"
        self._record(
            "file_change", "; ".join(paths) or "file change", "allow" if ok else "deny", rule
        )
        if not ok:
            self._round.declined += 1
        return "accept" if ok else "decline"

    async def _decide_command(self, params: Mapping[str, Any]) -> str:
        command = str(params.get("command") or "")
        cwd = params.get("cwd")
        detail = consent_detail(command)
        if isinstance(cwd, str) and cwd and not inside(self._round.root, cwd):
            decision = ConsentDecision("deny", "cwd_outside_root")
        else:
            decided = self._decide("run_shell", detail)
            decision = await decided if inspect.isawaitable(decided) else decided
        if not decision.asked:
            self._record("run_shell", detail, decision.answer, decision.rule)
        if decision.answer == "allow":
            return "accept"
        self._round.declined += 1
        return "decline"

    def _record(self, kind: str, detail: str, answer: str, rule: str) -> None:
        self._round.consents.append(
            ConsentDecisionRecord(
                kind, detail[:_DETAIL_CHARS], "allow" if answer == "allow" else "deny", rule
            )
        )

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


#: What to answer a request the round has no one to give: nothing granted, nothing chosen.
_DECLINES: dict[str, Mapping[str, Any]] = {
    "item/permissions/requestApproval": {"permissions": {}},
    "mcpServer/elicitation/request": {"action": "decline"},
    "applyPatchApproval": {"decision": "denied"},
    "execCommandApproval": {"decision": "denied"},
}


async def run_codex_app_server(
    *,
    binary: str,
    task_text: str,
    root: str,
    decide: Decider,
    sandbox: Sandbox,
    model: str | None = None,
    effort: str | None = None,
    env: Mapping[str, str] | None = None,
    env_passthrough: tuple[str, ...] = (),
    timeout: float | None = None,
) -> DelegationOutcome:
    """Run one delegation as one thread and one turn, and classify what it did.

    ``decide`` is asked about every command (``approvalPolicy: "untrusted"``) as ``("run_shell",
    "/bin/sh -c <line>")``, and may ask a person; it should return a
    :class:`~cuttlefish.delegate.consent.ConsentDecision`. A turn that passes ``timeout`` is
    interrupted (``turn/interrupt``, which also kills a running command) and reported as a
    ``round_timeout`` failure; a cancelled call interrupts it and re-raises.

    Raises :class:`DelegationError` for the binary missing or the server dying before the turn ends.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            "app-server",
            cwd=root,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merge_env(env, root=root, passthrough=env_passthrough),
        )
    except FileNotFoundError as exc:
        raise DelegationError(f"Codex binary {binary!r} not found") from exc

    round_ = _Round(root)
    connection = _Connection(process, round_, decide, edits=sandbox == "workspace-write")
    thread_id: str | None = None
    turn_id: str | None = None
    timed_out = False
    try:
        await connection.request(
            "initialize", {"clientInfo": {"name": "cuttlefish-crew", "version": "0"}}
        )
        connection.notify("initialized")
        started = await connection.request(
            "thread/start",
            {
                "cwd": root,
                "approvalPolicy": "untrusted",
                "sandbox": sandbox,
                **({"model": model} if model else {}),
            },
        )
        thread = started.get("thread")
        thread_id = thread.get("id") if isinstance(thread, Mapping) else None
        if not isinstance(thread_id, str):
            raise DelegationError("Codex app-server's thread/start returned no thread id")
        turn = await connection.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": task_text}],
                **({"effort": effort} if effort else {}),
            },
        )
        started_turn = turn.get("turn")
        turn_id = started_turn.get("id") if isinstance(started_turn, Mapping) else None
        try:
            await asyncio.wait_for(asyncio.shield(connection.turn_done), timeout)
        except TimeoutError:
            timed_out = True
            await _interrupt(connection, thread_id, turn_id)
    except asyncio.CancelledError:
        await asyncio.shield(_interrupt(connection, thread_id, turn_id))
        raise
    except DelegationError as exc:
        tail = await connection.stderr_tail()
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        raise DelegationError(f"{exc}; stderr: {tail or '<empty>'}" if tail else str(exc)) from exc
    finally:
        await asyncio.shield(connection.close())

    outcome = round_.outcome()
    if timed_out:
        limit = "its time limit" if timeout is None else f"{timeout:g} seconds"
        return dataclasses.replace(
            outcome,
            kind="failed",
            summary="Codex was stopped: the round ran past its time limit (round_timeout)",
            reason=f"stopped: the round ran longer than {limit}",
            failure_kind="round_timeout",
        )
    return outcome


async def _interrupt(connection: _Connection, thread_id: str | None, turn_id: str | None) -> None:
    """Ask Codex to end the turn and wait for its ``turn/completed``; best effort."""
    if thread_id is None or turn_id is None:
        return
    with contextlib.suppress(Exception):
        await asyncio.wait_for(
            connection.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}),
            _INTERRUPT_GRACE,
        )
        await asyncio.wait_for(asyncio.shield(connection.turn_done), _INTERRUPT_GRACE)
