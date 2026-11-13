"""The methods an ACP agent may call on its client: ``fs/read_text_file``, ``fs/write_text_file``
and the ``terminal/*`` family (CUT-266). Advertised in ``initialize`` so an agent that supports them
does its file access and commands through cuttlefish, where the role's policy applies instead of
the agent's own judgment. Files only inside the project root (a write never for a read-only role);
a command goes through the same shell policy as a ``session/request_permission`` of kind
``execute``, and the variables the agent asks to set for it are ignored."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import shlex
import signal
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from cuttlefish.agents.outcome import ConsentDecisionRecord, DelegationError
from cuttlefish.delegate.subprocess_env import merge_env

Json = dict[str, Any]

CLIENT_CAPABILITIES: Json = {
    "fs": {"readTextFile": True, "writeTextFile": True},
    "terminal": True,
}
_DEFAULT_OUTPUT_LIMIT = 1024 * 1024


def inside(path: str, root: str) -> bool:
    """``path`` is under ``root`` once ``..`` and symlinks are resolved (a write outside the root
    is never allowed)."""
    if not path:
        return False
    return os.path.realpath(path).startswith(os.path.realpath(root) + os.sep)


class _Terminal:
    def __init__(self, proc: asyncio.subprocess.Process, limit: int) -> None:
        self.proc = proc
        self.limit = limit
        self.output = bytearray()
        self.truncated = False
        self.reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        assert self.proc.stdout is not None
        while chunk := await self.proc.stdout.read(65536):
            self.output += chunk
            if len(self.output) > self.limit:
                del self.output[: len(self.output) - self.limit]
                self.truncated = True

    def exit_status(self) -> Json | None:
        code = self.proc.returncode
        if code is None:
            return None
        if code >= 0:
            return {"exitCode": code, "signal": None}
        return {"exitCode": None, "signal": signal.Signals(-code).name}

    def stop(self) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.proc.pid, signal.SIGKILL)


class ClientMethods:
    """One round's answers to the agent's file and terminal calls. ``decide_shell`` takes the
    command text and returns the policy's decision (possibly awaitable, when a person is asked)."""

    def __init__(
        self,
        *,
        root: str,
        mode: str,
        decide_shell: Callable[[str], Any],
        consents: list[ConsentDecisionRecord],
        secrets: Mapping[str, str],
    ) -> None:
        self._root = root
        self._mode = mode
        self._decide_shell = decide_shell
        self._consents = consents
        self._secrets = secrets
        self._terminals: dict[str, _Terminal] = {}
        self._count = 0

    async def handle(self, method: str, params: Json) -> Json:
        handler = {
            "fs/read_text_file": self._read_file,
            "fs/write_text_file": self._write_file,
            "terminal/create": self._create,
            "terminal/output": self._output,
            "terminal/wait_for_exit": self._wait,
            "terminal/kill": self._kill,
            "terminal/release": self._release,
        }.get(method)
        if handler is None:
            raise DelegationError(f"unsupported client method {method}")
        return await handler(params)

    def _file(self, params: Json, *, write: bool) -> str:
        path = str(params.get("path", ""))
        allowed = not (write and self._mode == "read-only") and inside(path, self._root)
        kind = "edit" if write else "read"
        rule = f"acp:fs_{kind}_in_root" if allowed else f"acp:fs_{kind}_not_allowed"
        self._consents.append(
            ConsentDecisionRecord(kind, path, "allow" if allowed else "deny", rule)
        )
        if not allowed:
            raise DelegationError(f"{kind} of {path!r} is not allowed")
        return path

    async def _read_file(self, params: Json) -> Json:
        path = self._file(params, write=False)
        lines = Path(path).read_text(errors="replace").splitlines(keepends=True)
        start = max(int(params.get("line") or 1) - 1, 0)
        limit = params.get("limit")
        end = start + int(limit) if limit is not None else None
        return {"content": "".join(lines[start:end])}

    async def _write_file(self, params: Json) -> Json:
        path = self._file(params, write=True)
        Path(path).write_text(str(params.get("content", "")))
        return {}

    async def _create(self, params: Json) -> Json:
        argv = [str(params.get("command", "")), *(str(a) for a in params.get("args") or [])]
        cwd = str(params.get("cwd") or self._root)
        # a shell line the agent wrote is judged as that line, as an ``execute`` permission is
        shell_line = (
            len(argv) == 3
            and Path(argv[0]).name in ("sh", "bash", "zsh")
            and argv[1] in ("-c", "-lc")
        )
        text = argv[2] if shell_line else shlex.join(argv)
        decision = self._decide_shell(text)
        if inspect.isawaitable(decision):
            decision = await decision
        in_root = os.path.realpath(cwd) == os.path.realpath(self._root) or inside(cwd, self._root)
        allowed = decision.answer == "allow" and in_root
        rule = decision.rule if in_root else "acp:cwd_outside_root"
        self._consents.append(
            ConsentDecisionRecord("execute", text, "allow" if allowed else "deny", rule)
        )
        if not allowed:
            raise DelegationError(f"running {text!r} is not allowed")
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
                env=merge_env(self._secrets, root=self._root),
                start_new_session=True,
            )
        except OSError as exc:
            raise DelegationError(f"could not run {argv[0]!r}: {exc}") from exc
        self._count += 1
        terminal_id = f"t{self._count}"
        limit = params.get("outputByteLimit")
        self._terminals[terminal_id] = _Terminal(
            proc, limit if isinstance(limit, int) and limit > 0 else _DEFAULT_OUTPUT_LIMIT
        )
        return {"terminalId": terminal_id}

    def _get(self, params: Json) -> _Terminal:
        terminal = self._terminals.get(str(params.get("terminalId")))
        if terminal is None:
            raise DelegationError(f"no terminal {params.get('terminalId')!r}")
        return terminal

    async def _output(self, params: Json) -> Json:
        terminal = self._get(params)
        return {
            "output": terminal.output.decode(errors="replace"),
            "truncated": terminal.truncated,
            "exitStatus": terminal.exit_status(),
        }

    async def _wait(self, params: Json) -> Json:
        terminal = self._get(params)
        await terminal.proc.wait()
        await terminal.reader
        return terminal.exit_status() or {}

    async def _kill(self, params: Json) -> Json:
        self._get(params).stop()
        return {}

    async def _release(self, params: Json) -> Json:
        terminal = self._terminals.pop(str(params.get("terminalId")), None)
        if terminal is not None:
            terminal.stop()
            terminal.reader.cancel()
        return {}

    def close(self) -> None:
        for terminal in self._terminals.values():
            terminal.stop()
            terminal.reader.cancel()
        self._terminals.clear()
