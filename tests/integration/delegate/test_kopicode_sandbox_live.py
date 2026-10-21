"""Integration: a real edit landing through the real kopicode binary, live,
routed through a real container sandbox instead of a bare scratch checkout.

The sandboxed counterpart to test_kopicode_live.py's own live proof (KAN-1008)
-- this is KAN-1010's, verified against the actual mechanism this suite hit
building it: a container does not inherit the host's environment (kopicode's own
credential has to be forwarded explicitly), and a bare base image typically
ships no CA bundle at all, so an outbound HTTPS call to a model provider fails
TLS verification unless one is provided. Both are fixed in
`cuttlefish.tasks.delegate`/`cuttlefish.sandbox.container`, not asserted here --
this is the regression test for "does a real, live delegation actually still
work once it's the sandboxed path, not just the direct one."
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from satay.api.primitives import start
from satay.journal.store import SQLiteStore

from cuttlefish import runtime
from cuttlefish.episodic.events import DelegationStarted, TaskCompleted
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.sandbox.container import ContainerSandboxProvider
from cuttlefish.tasks.delegate import delegate_to_agent_backend
from cuttlefish.workflow import run_task


def _init_scratch_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "cuttlefish-tests"], cwd=root, check=True)
    (root / "README.md").write_text("a scratch checkout for a live sandboxed kopicode test\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    yield
    runtime.reset()


@pytest.mark.requires_kopicode
@pytest.mark.requires_docker
@pytest.mark.requires_live_credential
async def test_a_real_write_lands_through_the_container_sandbox(tmp_path: Path) -> None:
    root = tmp_path / "scratch"
    root.mkdir()
    _init_scratch_repo(root)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=episodic_store,
            kopicode_binary="kopicode",
            sandbox_provider=ContainerSandboxProvider(),
        )
    )
    satay_store = SQLiteStore.open(":memory:")
    task_id = "test-task-sandboxed-live"

    handle = start(
        run_task,
        {
            "task_id": task_id,
            "text": (
                "Create a file named LIVE_SANDBOX_TEST.txt containing the single "
                "line: cuttlefish sandboxed live test"
            ),
            "root": str(root),
        },
        run_id=task_id,
        store=satay_store,
    )
    result = await handle.result()

    assert result["status"] == "completed"
    assert result["edited_paths"] == ["LIVE_SANDBOX_TEST.txt"]
    assert (root / "LIVE_SANDBOX_TEST.txt").read_text().strip() == (
        "cuttlefish sandboxed live test"
    )

    events = list(episodic_store.read(task_id))
    delegation_started = next(
        event.payload for event in events if isinstance(event.payload, DelegationStarted)
    )
    assert delegation_started.sandbox == "container"
    assert any(isinstance(event.payload, TaskCompleted) for event in events)

    episodic_store.close()
    satay_store.close()


@pytest.mark.requires_kopicode
@pytest.mark.requires_docker
async def test_delegate_to_agent_backend_forwards_the_kopicode_credential_into_the_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A container does not inherit the host's environment -- without this, a
    sandboxed delegation would always reach kopicode's own "no provider
    configured" outcome regardless of what credential the operator actually has,
    which is the exact gap a live run surfaced building KAN-1010. A garbage-but-
    present key still reaches a real outbound call to OpenRouter (verified live:
    it fails there, past the local "not set" check, distinctly and past kopicode's
    TLS handshake too -- which is what the CA-bundle mount is for), so this needs
    no real credential and makes no assertion about that call succeeding.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-not-a-real-key-but-set")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=store,
            kopicode_binary="kopicode",
            sandbox_provider=ContainerSandboxProvider(),
        )
    )
    root = tmp_path / "scratch"
    root.mkdir()

    outcome = await delegate_to_agent_backend("add a .gitignore entry", str(root))

    assert outcome.kind == "failed"
    assert "OPENROUTER_API_KEY is not set" not in (outcome.reason or "")

    store.close()
