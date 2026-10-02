"""Unit: ``ContainerSandboxProvider.spawn`` builds the right ``docker exec`` and hands back a
live, piped process (KAN-1793). A fake ``docker`` records its argv and runs ``cat``, so this
needs no daemon."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from cuttlefish.sandbox.container import ContainerSandboxProvider
from cuttlefish.sandbox.provider import (
    SandboxError,
    SandboxHandle,
    StreamingSandboxProvider,
)


@pytest.fixture
def fake_docker(tmp_path: Path) -> tuple[str, Path]:
    log = tmp_path / "argv.txt"
    script = tmp_path / "docker"
    script.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {log}\nexec cat\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script), log


async def test_spawn_is_docker_exec_dash_i_with_the_command_and_workdir(
    fake_docker: tuple[str, Path],
) -> None:
    binary, log = fake_docker
    provider = ContainerSandboxProvider(docker_binary=binary)
    process = await provider.spawn(
        SandboxHandle("c1"), ["/usr/local/bin/kopicode", "serve"], cwd="/work"
    )
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(b'{"ping":1}\n')
    await process.stdin.drain()
    assert await process.stdout.readline() == b'{"ping":1}\n'  # a piped, live process
    process.stdin.close()
    await process.wait()

    argv = log.read_text().splitlines()
    assert argv == ["exec", "-i", "-w", "/work", "c1", "/usr/local/bin/kopicode", "serve"]
    assert "-t" not in argv and "-it" not in argv  # a terminal would mangle the line framing


async def test_spawn_without_a_workdir_omits_dash_w(fake_docker: tuple[str, Path]) -> None:
    binary, log = fake_docker
    process = await ContainerSandboxProvider(docker_binary=binary).spawn(
        SandboxHandle("c1"), ["kopicode", "serve"]
    )
    assert process.stdin is not None
    process.stdin.close()
    await process.wait()
    assert log.read_text().splitlines() == ["exec", "-i", "c1", "kopicode", "serve"]


async def test_a_docker_binary_that_vanishes_is_a_sandbox_error(
    fake_docker: tuple[str, Path],
) -> None:
    binary, _ = fake_docker
    provider = ContainerSandboxProvider(docker_binary=binary)
    Path(binary).unlink()
    with pytest.raises(SandboxError):
        await provider.spawn(SandboxHandle("c1"), ["kopicode", "serve"])


def test_the_container_provider_is_a_streaming_provider(fake_docker: tuple[str, Path]) -> None:
    assert isinstance(
        ContainerSandboxProvider(docker_binary=fake_docker[0]), StreamingSandboxProvider
    )


def test_e2b_is_not_a_streaming_provider() -> None:
    from cuttlefish.sandbox.e2b import E2bSandboxProvider

    assert not isinstance(E2bSandboxProvider(api_key="x"), StreamingSandboxProvider)
