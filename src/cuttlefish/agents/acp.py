"""An :class:`~cuttlefish.agents.backend.AgentBackend` over the Agent Client Protocol, JSON-RPC
2.0 over an agent subprocess's stdio (CUT-232 spike, CUT-260 shipped as the fourth backend).

The agent is whatever ``CUTTLEFISH_ACP_AGENT`` names. Findings:
``docs/research/acp-backend-spike.md``.
A wrapped agent keeps its own tool's login, which the credential broker (ADR-0031) cannot hide.
This wraps the protocol as published (ADR-0003, ADR-0005):
``initialize``, ``session/new``, ``session/prompt`` with streamed ``session/update``s, the
agent's ``session/request_permission`` answered by the role's policy, ``session/cancel`` on a
timeout. ``session/load`` continues a role's earlier session (``CUTTLEFISH_RESUME=1``) when the
agent advertises ``loadSession``.

Gaps, on purpose: no sandbox provider, no credential broker, no fs/terminal client methods
(the agent uses its own tools), no usage figure beyond ``usage_update``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, ClassVar

from cuttlefish.agents.deciders import command_decider
from cuttlefish.agents.outcome import (
    ConsentDecisionRecord,
    DelegationError,
    DelegationOutcome,
    ToolCallRecord,
)
from cuttlefish.delegate import conversations
from cuttlefish.delegate.subprocess_env import merge_env
from cuttlefish.requests import ShellAsker
from cuttlefish.sandbox.provider import SandboxProvider

PROTOCOL_VERSION = 1
_LIMIT = 8 * 1024 * 1024
_EDIT_KINDS = frozenset({"edit", "delete", "move"})

Json = dict[str, Any]


def _pick(options: list[Json], *kinds: str) -> str | None:
    for kind in kinds:
        for option in options:
            if option.get("kind") == kind and isinstance(option.get("optionId"), str):
                return str(option["optionId"])
    return None


def _inside(path: str, root: str) -> bool:
    """``path`` is under ``root`` once ``..`` and symlinks are resolved (a write outside the root
    is never allowed)."""
    if not path:
        return False
    return os.path.realpath(path).startswith(os.path.realpath(root) + os.sep)


class _Conn:
    """One agent process and its JSON-RPC traffic. Requests from the agent are answered on a
    task each, so a permission held for a person never stalls the reader."""

    def __init__(
        self,
        proc: asyncio.subprocess.Process,
        on_request: Callable[[str, Json], Awaitable[Json]],
        on_notify: Callable[[str, Json], None],
    ) -> None:
        self._proc = proc
        self._on_request = on_request
        self._on_notify = on_notify
        self._next = 0
        self._pending: dict[int, asyncio.Future[Json]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._reader = asyncio.create_task(self._read())

    async def _write(self, message: Json) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write((json.dumps(message) + "\n").encode())
        await self._proc.stdin.drain()

    async def call(self, method: str, params: Json) -> Json:
        self._next += 1
        future: asyncio.Future[Json] = asyncio.get_running_loop().create_future()
        self._pending[self._next] = future
        await self._write({"jsonrpc": "2.0", "id": self._next, "method": method, "params": params})
        return await future

    async def notify(self, method: str, params: Json) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _answer(self, message: Json) -> None:
        try:
            result = await self._on_request(str(message["method"]), message.get("params") or {})
            reply: Json = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        except Exception as exc:  # the agent gets an error, never silence
            reply = {
                "jsonrpc": "2.0",
                "id": message["id"],
                "error": {"code": -32601, "message": str(exc)},
            }
        with contextlib.suppress(Exception):
            await self._write(reply)

    async def _read(self) -> None:
        assert self._proc.stdout is not None
        try:
            while line := await self._proc.stdout.readline():
                try:
                    message = json.loads(line)
                except ValueError:
                    continue  # agents may print non-protocol lines on stdout
                if not isinstance(message, dict):
                    continue
                if "method" in message and "id" in message:
                    task = asyncio.create_task(self._answer(message))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
                elif "method" in message:
                    self._on_notify(str(message["method"]), message.get("params") or {})
                elif (future := self._pending.pop(message.get("id", -1), None)) is not None:
                    if "error" in message:
                        err = message["error"]
                        future.set_exception(
                            DelegationError(f"ACP error {err.get('code')}: {err.get('message')}")
                        )
                    else:
                        future.set_result(message.get("result") or {})
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(DelegationError("the ACP agent closed its stdout"))

    async def close(self) -> None:
        self._reader.cancel()
        for task in self._tasks:
            task.cancel()
        if self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._proc.kill()
        with contextlib.suppress(Exception):
            await self._proc.wait()


class AcpBackend:
    """Drives any ACP agent started as ``<binary> <args...>`` (the agent's own ACP entry point)."""

    NAME: ClassVar[str] = "acp"
    CREDENTIAL_ENV_VARS: ClassVar[tuple[str, ...]] = ()

    def __init__(self, binary: str, args: tuple[str, ...] = (), *, timeout: float = 600.0) -> None:
        self._binary = binary
        self._args = args
        self._timeout = timeout
        #: Capabilities from the last ``initialize``, for the findings and for tests.
        self.agent_capabilities: Json = {}

    async def delegate(
        self,
        *,
        task_text: str,
        root: str,
        allow: list[list[str]] | None,
        secrets: Mapping[str, str],
        sandbox_provider: SandboxProvider | None,
        mode: str = "standard",
        asker: ShellAsker | None = None,
        session: str | None = None,
    ) -> DelegationOutcome:
        """``session`` is the conversation key (normally the role's name). With
        ``CUTTLEFISH_RESUME=1`` and an agent that advertises ``loadSession``, the id kept for it in
        the project root (``cuttlefish.delegate.conversations``) is ``session/load``-ed; a load the
        agent refuses starts a new session. The session id is also in the outcome's ``record``."""
        if sandbox_provider is not None:
            raise DelegationError("the ACP backend does not run inside a sandbox provider")
        decide = command_decider(allow, mode, asker)
        try:
            proc = await asyncio.create_subprocess_exec(
                self._binary,
                *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=root,
                env=merge_env(secrets, root=root),
                limit=_LIMIT,
            )
        except OSError as exc:
            raise DelegationError(f"ACP agent {self._binary!r} could not start: {exc}") from exc

        text: list[str] = []
        calls: dict[str, ToolCallRecord] = {}
        edited: list[str] = []
        consents: list[ConsentDecisionRecord] = []
        tokens: list[int] = []
        cost: list[float] = []
        state = {"replaying": False}

        def on_notify(method: str, params: Json) -> None:
            if method != "session/update" or state["replaying"]:
                return
            update = params.get("update") or {}
            kind = update.get("sessionUpdate")
            if kind == "agent_message_chunk":
                block = update.get("content") or {}
                if block.get("type") == "text":
                    text.append(str(block.get("text", "")))
            elif kind in ("tool_call", "tool_call_update"):
                self._track(calls, edited, update)
            elif kind == "usage_update":
                if isinstance(update.get("used"), int):
                    tokens.append(update["used"])
                amount = (update.get("cost") or {}).get("amount")
                if isinstance(amount, (int, float)):
                    cost.append(float(amount))

        async def on_request(method: str, params: Json) -> Json:
            if method != "session/request_permission":
                raise DelegationError(f"unsupported client method {method}")
            return await self._permission(params, root, mode, decide, calls, consents)

        conn = _Conn(proc, on_request, on_notify)
        session_id = ""
        try:
            init = await asyncio.wait_for(
                conn.call(
                    "initialize", {"protocolVersion": PROTOCOL_VERSION, "clientCapabilities": {}}
                ),
                30,
            )
            self.agent_capabilities = init.get("agentCapabilities") or {}
            if init.get("authMethods"):
                # Auth is the agent's and the operator's business; cuttlefish holds no login.
                names = ", ".join(str(m.get("id")) for m in init["authMethods"])
                raise DelegationError(f"ACP agent wants authentication first ({names})")
            params: Json = {"cwd": root, "mcpServers": []}
            key = session or conversations.DEFAULT_KEY
            resuming = conversations.resume_enabled(self.NAME)
            remembered = (
                conversations.recall(root, self.NAME, key)
                if resuming and self.agent_capabilities.get("loadSession")
                else None
            )
            session_id = ""
            if remembered:
                state["replaying"] = True
                try:
                    await conn.call("session/load", {"sessionId": remembered, **params})
                    session_id = remembered
                except DelegationError:
                    conversations.forget(root, self.NAME, key)
                state["replaying"] = False
            if not session_id:
                session_id = str((await conn.call("session/new", params))["sessionId"])
            if resuming:
                conversations.remember(root, self.NAME, key, session_id)
            prompt = asyncio.ensure_future(
                conn.call(
                    "session/prompt",
                    {"sessionId": session_id, "prompt": [{"type": "text", "text": task_text}]},
                )
            )
            done, _ = await asyncio.wait({prompt}, timeout=self._timeout)
            if not done:
                await conn.notify("session/cancel", {"sessionId": session_id})
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(prompt, 10)
                prompt.cancel()
                return self._outcome(
                    "failed",
                    text,
                    edited,
                    calls,
                    consents,
                    tokens,
                    cost,
                    session_id,
                    "timeout",
                    f"no answer in {self._timeout:.0f}s",
                )
            stop = str(prompt.result().get("stopReason", ""))
        except (DelegationError, TimeoutError) as exc:
            if isinstance(exc, TimeoutError):
                exc = DelegationError("the ACP agent did not answer initialize")
            raise exc from None
        finally:
            await conn.close()

        denied = any(c.answer == "deny" for c in consents)
        if stop == "refusal" or (denied and not edited):
            kind = "refused"
        elif stop in ("end_turn",):
            kind = "completed"
        else:
            kind = "failed"
        return self._outcome(
            kind,
            text,
            edited,
            calls,
            consents,
            tokens,
            cost,
            session_id,
            None if kind == "completed" else stop,
            None,
        )

    @staticmethod
    def _track(calls: dict[str, ToolCallRecord], edited: list[str], update: Json) -> None:
        call_id = str(update.get("toolCallId"))
        old = calls.get(call_id)
        title = str(update.get("title") or (old.detail if old else ""))
        tool = str(update.get("kind") or (old.tool if old else "other"))
        status = update.get("status")
        if status == "completed":
            record = ToolCallRecord(tool, title, "ok")
            if tool in _EDIT_KINDS:
                for loc in update.get("locations") or []:
                    if isinstance(loc.get("path"), str) and loc["path"] not in edited:
                        edited.append(loc["path"])
        elif status == "failed":
            # A call we rejected is reported failed by the agent; keep it "denied".
            record = ToolCallRecord(
                tool, title, "denied" if old and old.status == "denied" else "error"
            )
        else:
            record = ToolCallRecord(tool, title, old.status if old else "ok")
        calls[call_id] = record

    @staticmethod
    async def _permission(
        params: Json,
        root: str,
        mode: str,
        decide: Callable[[str, str], Any],
        calls: dict[str, ToolCallRecord],
        consents: list[ConsentDecisionRecord],
    ) -> Json:
        """Map the role's policy onto the options the agent offered. Only ``execute`` goes through
        the shell policy; edits inside the root are allowed (not for a read-only role), anything
        else is denied. An agent that offers no reject option gets ``cancelled``, which the
        protocol defines for that."""
        options = [o for o in params.get("options") or [] if isinstance(o, dict)]
        call = params.get("toolCall") or {}
        kind = str(call.get("kind") or "other")
        detail = str(call.get("title") or "")
        if kind == "execute":
            command = (call.get("rawInput") or {}).get("command", detail)
            if isinstance(command, list):
                command = shlex.join(str(c) for c in command)
            decision = decide("run_shell", f"/bin/sh -c {command}")
            if inspect_awaitable(decision):
                decision = await decision
            allowed, rule = decision.answer == "allow", decision.rule
        elif kind in ("read", "search", "think") or (
            kind in _EDIT_KINDS
            and mode != "read-only"
            and all(
                _inside(str(loc.get("path", "")), root) for loc in call.get("locations") or [{}]
            )
        ):
            allowed, rule = True, f"acp:{kind}_in_root"
        else:
            allowed, rule = False, f"acp:{kind}_not_allowed"
        consents.append(ConsentDecisionRecord(kind, detail, "allow" if allowed else "deny", rule))
        if not allowed:
            record = calls.get(str(call.get("toolCallId")))
            calls[str(call.get("toolCallId"))] = ToolCallRecord(
                kind, detail or (record.detail if record else ""), "denied"
            )
        option = _pick(options, *(("allow_once",) if allowed else ("reject_once", "reject_always")))
        if option is None:
            return {"outcome": {"outcome": "cancelled"}}
        return {"outcome": {"outcome": "selected", "optionId": option}}

    @staticmethod
    def _outcome(
        kind: Any,
        text: list[str],
        edited: list[str],
        calls: dict[str, ToolCallRecord],
        consents: list[ConsentDecisionRecord],
        tokens: list[int],
        cost: list[float],
        session_id: str,
        reason: str | None,
        detail: str | None,
    ) -> DelegationOutcome:
        return DelegationOutcome(
            kind=kind,
            summary="".join(text).strip()[-2000:],
            edited_paths=edited,
            reason=reason,
            tokens=tokens[-1] if tokens else None,
            cost_usd=cost[-1] if cost else None,
            tool_calls=list(calls.values()),
            record=session_id or None,
            detail=detail,
            consent_decisions=consents,
        )


def inspect_awaitable(value: object) -> bool:
    return asyncio.iscoroutine(value) or isinstance(value, asyncio.Future)
