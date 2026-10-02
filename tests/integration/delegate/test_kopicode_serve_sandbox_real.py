"""Integration: ``kopicode serve`` inside a real container, through the real backend (KAN-1793).

No model credential is spent: an invalid key is one of kopicode's documented outcomes, and
it still walks the whole real path -- ``docker run``, ``docker exec -i``, ``session.start``,
the turn, ``session.close`` and the stdin-EOF shutdown, with the provider's HTTP status coming
back in ``session_ended.text``. Needs docker, outbound network, and a kopicode with serve's
consent mode (>= v0.2.0). The live allow/deny proof is KAN-1794's, which needs a real key.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cuttlefish.agents.kopicode import KopicodeBackend
from cuttlefish.sandbox.container import ContainerSandboxProvider
from cuttlefish.sandbox.provider import SandboxHandle, SandboxSpec

_INVALID_OPENROUTER_KEY = "sk-or-v1-" + "0" * 64


class _RecordingProvider(ContainerSandboxProvider):
    """The real container provider, remembering which containers it made."""

    def __init__(self) -> None:
        super().__init__()
        self.created: list[str] = []

    async def create(self, spec: SandboxSpec | None = None) -> SandboxHandle:
        handle = await super().create(spec)
        self.created.append(handle.id)
        return handle


def _container_exists(container_id: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", "--type", "container", container_id],
        capture_output=True,
        check=False,
        cwd="/",
        env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    return result.returncode == 0


@pytest.mark.requires_kopicode
@pytest.mark.requires_docker
async def test_serve_runs_in_a_container_and_the_container_is_gone_afterwards(
    tmp_path: Path,
) -> None:
    env = {"PATH": "/usr/bin:/bin"}
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=env, cwd=tmp_path)
    provider = _RecordingProvider()

    outcome = await KopicodeBackend("kopicode").delegate(
        task_text="add a .gitignore entry",
        root=str(tmp_path),
        allow=None,
        secrets={"OPENROUTER_API_KEY": _INVALID_OPENROUTER_KEY},
        sandbox_provider=provider,
    )

    assert outcome.kind == "failed"
    assert outcome.failure_kind == "provider_auth"  # the real serve wire, end to end
    assert outcome.reason is not None and _INVALID_OPENROUTER_KEY not in outcome.reason
    assert len(provider.created) == 1
    assert not _container_exists(provider.created[0])  # destroy() ran
