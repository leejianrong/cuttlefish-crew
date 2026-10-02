"""The ``SandboxProvider`` seam for real process/container containment (ADR-0002, Q12).

The interface itself: create, exec, snapshot, destroy, loosely matching the
shape OpenAI's Agents SDK already standardised across seven sandbox providers,
so this project is compatible with an emerging convention rather than inventing
its own (QUESTIONS.md Q12). ``cuttlefish.tasks.delegate`` (docs/SLICES.md V2
step 2, KAN-1010) routes the kopicode delegation through whichever backend is
configured on ``runtime.Runtime.sandbox_provider``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import ClassVar, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SandboxHandle:
    """One live sandbox, opaque past its own id — a provider's own concern."""

    id: str


@dataclass(frozen=True, slots=True)
class ExecResult:
    """One completed ``exec`` call. A non-zero ``exit_code`` is not an error here —
    the caller decides what a given command's failure means, the same way
    ``subprocess.run`` reports it rather than raising."""

    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class SnapshotHandle:
    """A named, resumable capture of a sandbox's filesystem state at one point in
    time. Restoring one into a new sandbox is not part of this interface yet —
    ADR-0002 defers that (a snapshot tied to a satay journal turn) to a fresh ADR
    once there is real fork-and-replay usage to build it against."""

    id: str


@dataclass(frozen=True, slots=True)
class SandboxSpec:
    """What ``create`` asks a provider to start, kept separate from the handle it
    returns so a caller's request and a provider's own bookkeeping never share one
    type.

    ``mounts`` (host path -> in-sandbox path) is honoured by
    :class:`~cuttlefish.sandbox.container.ContainerSandboxProvider` as a real bind
    mount — the only backend that can, since it runs on the same host. There is
    no equivalent for a remote backend like E2B (there is no local filesystem to
    bind into a remote microVM); :class:`~cuttlefish.sandbox.e2b.E2bSandboxProvider`
    raises :class:`SandboxError` for a non-empty ``mounts`` rather than silently
    ignoring it, so a caller that switches backends fails loudly instead of
    getting a sandbox that quietly can't see the files it asked for.
    """

    template: str | None = None
    timeout: float | None = None
    envs: Mapping[str, str] = field(default_factory=dict)
    mounts: Mapping[str, str] = field(default_factory=dict)


class SandboxError(Exception):
    """A sandbox operation couldn't be completed — the provider's own backend
    rejected it, or the handle it was given no longer refers to anything live."""


class SandboxProvider(Protocol):
    """A cuttlefish-facing sandbox provider: create, exec, snapshot, destroy.

    This Protocol exists so the kopicode delegation (V2 step 2) depends on this
    shape, not on any one backend's own client directly.

    ``BACKEND_NAME`` is this project's own small addition, not part of the
    OpenAI Agents SDK shape it otherwise mirrors: the episodic journal records
    which backend actually ran a delegation (``DelegationStarted.sandbox``), and
    that has to come from somewhere stable, not a Python class name a refactor
    could quietly change.
    """

    BACKEND_NAME: ClassVar[str]

    async def create(self, spec: SandboxSpec | None = None) -> SandboxHandle: ...

    async def exec(
        self,
        handle: SandboxHandle,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult: ...

    async def snapshot(self, handle: SandboxHandle) -> SnapshotHandle: ...

    async def destroy(self, handle: SandboxHandle) -> None: ...


@runtime_checkable
class StreamingSandboxProvider(Protocol):
    """A provider that can also start a process in a sandbox and keep talking to it
    (KAN-1793, ADR-0021) -- the capability ``exec`` lacks, since ``exec`` returns only after
    the process exits.

    Separate from :class:`SandboxProvider` on purpose: a backend opts in, and a caller
    checks once with ``isinstance`` and otherwise falls back to ``exec``. A provider that
    cannot stream (E2B today) is not forced to implement or half-implement it.

    ``spawn`` returns the started process with stdin, stdout and stderr piped and no
    terminal -- the caller owns its lifetime and the stream's framing. **Killing the
    returned process does not promise the command inside the sandbox stops**: for a
    container it ends only the local client (verified: the in-container process survived
    a SIGKILL of ``docker exec -i``). The caller's real cleanup is :meth:`destroy`.
    """

    async def spawn(
        self,
        handle: SandboxHandle,
        command: Sequence[str],
        *,
        cwd: str | None = None,
    ) -> asyncio.subprocess.Process: ...
