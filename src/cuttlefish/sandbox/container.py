"""A container-backed ``SandboxProvider`` (ADR-0002's 2026-08-26 addendum): real
process/container containment via a local Docker daemon, needing no external
account or credential — unlike :class:`~cuttlefish.sandbox.e2b.E2bSandboxProvider`,
usable the moment a task needs real containment.

Shells out to the ``docker`` CLI directly, the same posture this project already
takes toward kopicode (ADR-0003) rather than adding a client SDK: one external
binary, invoked as-is, no new protocol of this project's own.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from cuttlefish.sandbox.provider import (
    ExecResult,
    SandboxError,
    SandboxHandle,
    SandboxSpec,
    SnapshotHandle,
)

DEFAULT_IMAGE = "debian:bookworm-slim"
DEFAULT_DOCKER_BINARY = "docker"

#: Bind-mounted read-only into every container this backend starts, from the
#: docker *daemon's* own host filesystem (a bind mount's host side always
#: resolves there, not the `docker` CLI client's — the same filesystem on
#: Linux, the VM Docker Desktop runs on macOS/Windows). Outbound HTTPS (e.g.
#: kopicode calling a model provider) needs a CA bundle, and a bare OS base
#: image typically doesn't ship one — verified empirically against this
#: backend's own default image and a couple of others (neither
#: debian:bookworm-slim nor ubuntu:24.04 do; python:3.12-slim happens to,
#: incidentally, as a side effect of something else it installs). Any host
#: that can successfully `docker pull` already has a working CA bundle
#: *somewhere* — Docker itself needs one to fetch images over HTTPS — so
#: reusing it here needs no new dependency, network access, or package
#: install at sandbox-creation time. Skipped, not required, if this host
#: doesn't have one at the standard path; a caller's own image may already
#: carry a valid bundle of its own.
_HOST_CA_BUNDLE = Path("/etc/ssl/certs/ca-certificates.crt")

#: docker's own stderr wording when a container id no longer refers to anything
#: live — distinguishes "this handle is stale" (a SandboxError) from "the
#: command we ran inside the container failed" (a normal, reportable ExecResult).
_NO_SUCH_CONTAINER = "No such container"


class DockerNotAvailableError(RuntimeError):
    """``docker`` isn't on PATH (checked before the first call, not mid-task)."""


class ContainerSandboxProvider:
    """``SandboxProvider`` over a local Docker daemon, via the ``docker`` CLI.

    Every sandbox is one detached container running ``sleep`` for `timeout`
    seconds (or indefinitely, with none declared) — a real process to `exec`
    into, and a hard lifetime matching E2B's own `timeout` semantics (a sandbox
    TTL, not an idle timeout) rather than a new concept of this module's own.
    """

    BACKEND_NAME: ClassVar[str] = "container"

    def __init__(
        self, *, image: str | None = None, docker_binary: str = DEFAULT_DOCKER_BINARY
    ) -> None:
        if shutil.which(docker_binary) is None:
            raise DockerNotAvailableError(f"{docker_binary!r} is not on PATH")
        self._image = image or DEFAULT_IMAGE
        self._docker_binary = docker_binary

    async def create(self, spec: SandboxSpec | None = None) -> SandboxHandle:
        resolved_spec = spec if spec is not None else SandboxSpec()
        sleep_for = (
            str(int(resolved_spec.timeout)) if resolved_spec.timeout is not None else "infinity"
        )
        args = ["run", "-d"]
        if hasattr(os, "getuid"):
            # Container root has no host-side identity of its own: a bind mount
            # is the same host inode either way, so anything kopicode creates
            # inside one as root lands on the host owned by root too, exactly
            # as unremovable by the operator's own user as it would be if
            # kopicode had somehow run as root on the host directly (verified
            # live: an earlier run left a root-owned `.kopicode/` in a scratch
            # checkout a normal user couldn't clean up). Running as the
            # operator's own uid/gid instead means anything created inside a
            # bind-mounted host path is owned by the same user who created the
            # scratch checkout in the first place — not expressible on a
            # platform with no POSIX uid (`os.getuid` absent), where this is
            # simply skipped rather than failing.
            args += ["-u", f"{os.getuid()}:{os.getgid()}"]
        if _HOST_CA_BUNDLE.is_file():
            args += ["-v", f"{_HOST_CA_BUNDLE}:{_HOST_CA_BUNDLE}:ro"]
        for key, value in resolved_spec.envs.items():
            args += ["-e", f"{key}={value}"]
        for host_path, sandbox_path in resolved_spec.mounts.items():
            args += ["-v", f"{host_path}:{sandbox_path}"]
        args += [resolved_spec.template or self._image, "sleep", sleep_for]

        code, stdout, stderr = await self._docker(*args)
        if code != 0:
            raise SandboxError(f"docker run failed: {stderr.strip()}")
        return SandboxHandle(id=stdout.strip())

    async def exec(
        self,
        handle: SandboxHandle,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        args = ["exec"]
        if cwd is not None:
            args += ["-w", cwd]
        args += [handle.id, *command]

        code, stdout, stderr = await self._docker(*args, timeout=timeout)
        if code != 0 and _NO_SUCH_CONTAINER in stderr:
            raise SandboxError(f"container {handle.id!r} not found: {stderr.strip()}")
        return ExecResult(exit_code=code, stdout=stdout, stderr=stderr)

    async def spawn(
        self,
        handle: SandboxHandle,
        command: Sequence[str],
        *,
        cwd: str | None = None,
    ) -> asyncio.subprocess.Process:
        """``docker exec -i``: stdin and stdout piped, never ``-t`` (a terminal would mangle
        a line-framed protocol). The returned process is the local ``docker`` client; ending
        it does not promise the command in the container ends -- ``destroy`` does."""
        args = ["exec", "-i"]
        if cwd is not None:
            args += ["-w", cwd]
        args += [handle.id, *command]
        try:
            return await asyncio.create_subprocess_exec(
                self._docker_binary,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise SandboxError(f"docker exec -i failed to start: {exc}") from exc

    async def snapshot(self, handle: SandboxHandle) -> SnapshotHandle:
        code, stdout, stderr = await self._docker("commit", handle.id)
        if code != 0:
            raise SandboxError(f"docker commit failed for {handle.id!r}: {stderr.strip()}")
        return SnapshotHandle(id=stdout.strip())

    async def destroy(self, handle: SandboxHandle) -> None:
        code, _stdout, stderr = await self._docker("rm", "-f", handle.id)
        if code != 0 and _NO_SUCH_CONTAINER not in stderr:
            raise SandboxError(f"docker rm failed for {handle.id!r}: {stderr.strip()}")

    async def _docker(self, *args: str, timeout: float | None = None) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            self._docker_binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise SandboxError(f"docker {args[0]} timed out after {timeout}s") from exc
        assert process.returncode is not None
        return (
            process.returncode,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
        )
