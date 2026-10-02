"""Real process/container containment for the kopicode delegation (ADR-0002).

Not wired into the delegation yet — see `docs/SLICES.md` V2 and this package's
own modules for what's built and what's still ahead.
"""

from __future__ import annotations

from cuttlefish.sandbox.container import ContainerSandboxProvider, DockerNotAvailableError
from cuttlefish.sandbox.e2b import E2bSandboxProvider, MissingApiKeyError
from cuttlefish.sandbox.provider import (
    ExecResult,
    SandboxError,
    SandboxHandle,
    SandboxProvider,
    SandboxSpec,
    SnapshotHandle,
    StreamingSandboxProvider,
)

__all__ = [
    "ContainerSandboxProvider",
    "DockerNotAvailableError",
    "E2bSandboxProvider",
    "ExecResult",
    "MissingApiKeyError",
    "SandboxError",
    "SandboxHandle",
    "SandboxProvider",
    "SandboxSpec",
    "SnapshotHandle",
    "StreamingSandboxProvider",
]
