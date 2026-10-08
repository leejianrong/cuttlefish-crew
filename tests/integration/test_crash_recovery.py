"""Crash recovery (docs/SLICES.md V1 build plan step 7, ADR-0001, PLAN.md R1).

"The single most important test in this slice" per SLICES.md — the acceptance
criterion for R1, and the thing that actually validates ADR-0001's whole argument
for using satay at all: kill the process after a chosen journal event, resume, and
reach the same terminal state without re-running a delegation that already
completed.

Two tests, at two grains:

- ``test_delegation_is_not_invoked_twice_after_a_crash`` isolates exactly the
  property R1 states — kopicode is not invoked a second time for the same
  logical call — using a minimal single-task workflow around the real
  ``delegate_to_agent_backend`` task. satay's own ``FaultInjector`` crashes on the
  *next* commit of a given journal event type (it has no "skip N, then crash"
  mode), so this stays isolated rather than trying to land the crash at a
  specific ordinal inside the full, multi-task ``run_task`` workflow.
- ``test_full_workflow_resumes_to_the_same_terminal_state_after_a_crash`` drives
  the real, complete workflow and crashes after its first satay-level
  ``TaskCompleted`` (``run_task``'s first durable call, journaling
  ``TaskSubmitted``), proving the whole pipeline — not just the delegation task
  in isolation — survives and reaches the correct terminal state without a
  duplicated episodic write.

No mock kopicode (docs/PLAN.md "Testing approach"). The first test needs
kopicode's own session to actually *complete* (successfully or not) so its
outcome is a returned ``DelegationOutcome`` rather than a raised
``DelegationError`` — only a task that returns commits a satay ``TaskCompleted``
event at all (a task whose body raises never does, satay's own
``replay/engine.py`` ``_execute``: the non-collecting path re-raises before any
event is committed). A missing credential fails *before* kopicode's session ever
opens, so it can't be used here; a syntactically-valid-but-wrong one reaches a
real ``session_ended`` over a real (rejected, uncharged) network round trip,
which is what this test uses. The second test doesn't need that — it targets
``run_task``'s first durable call, a plain journal append that always succeeds
regardless of any provider credential.
"""

from __future__ import annotations

import shutil
import stat
from pathlib import Path

import pytest
import satay
from satay.journal.store import SQLiteStore
from satay.testing.faults import FaultInjector, SimulatedCrash

from cuttlefish import runtime
from cuttlefish.delegate.kopicode import DelegationOutcome
from cuttlefish.episodic.events import (
    DelegationFailed,
    DelegationStarted,
    TaskFailed,
    TaskSubmitted,
)
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.llm.replay import ReplayLlmProvider
from cuttlefish.tasks.delegate import delegate_to_agent_backend
from cuttlefish.workflow import run_task


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    yield
    runtime.reset()


def _counting_kopicode_wrapper(tmp_path: Path, real_binary: str) -> tuple[str, Path]:
    """A shell script that execs the real kopicode, first bumping a counter file.

    Still the real binary underneath -- this observes invocation count without
    faking any of kopicode's own behaviour, so it doesn't compromise "no mock
    kopicode" (docs/PLAN.md "Testing approach").
    """
    counter_file = tmp_path / "invocations"
    counter_file.write_text("0")
    wrapper = tmp_path / "kopicode-counting-wrapper"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        # `version --json` is cuttlefish's feature probe, not a delegation: do not count it.
        f'if [ "${{1:-}}" = version ]; then exec "{real_binary}" "$@"; fi\n'
        f'count=$(cat "{counter_file}")\n'
        f'echo $((count + 1)) > "{counter_file}"\n'
        f'exec "{real_binary}" "$@"\n'
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    return str(wrapper), counter_file


#: A syntactically plausible but real-world-invalid key. kopicode's session opens
#: normally, sends one real request, and gets a real 401 back -- no cost, no
#: credential ever leaked (this value is fake), and (crucially for this test) a
#: real, recorded `session_ended`.
_INVALID_OPENROUTER_KEY = "sk-or-v1-" + "0" * 64


@satay.workflow
async def _delegate_only(task_input: dict[str, str]) -> DelegationOutcome:
    """A minimal, single-durable-call workflow isolating the delegation task.

    Exists only so FaultInjector's "crash on the next commit of this event type"
    lands exactly on this one task's own completion, not some other task's — see
    the module docstring.
    """
    return await delegate_to_agent_backend(task_input["text"], task_input["root"])


@pytest.mark.requires_kopicode
async def test_delegation_is_not_invoked_twice_after_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Needs outbound network access (see the module docstring)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", _INVALID_OPENROUTER_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    real_binary = shutil.which("kopicode")
    assert real_binary is not None
    wrapper, counter_file = _counting_kopicode_wrapper(tmp_path, real_binary)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=episodic_store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary=wrapper,
        )
    )
    root = tmp_path / "scratch"
    root.mkdir()
    task_input = {"text": "add a .gitignore entry", "root": str(root)}
    database = tmp_path / "satay.db"

    # -- Phase 1: crash right after delegate_to_agent_backend's own TaskCompleted. -----
    store = SQLiteStore.open(database)
    injector = FaultInjector()
    injector.crash_after("TaskCompleted")
    handle = satay.start(
        _delegate_only, task_input, run_id="crash-test-1", store=store, injector=injector
    )
    with pytest.raises(SimulatedCrash):
        await handle.result()
    store.close()

    assert counter_file.read_text().strip() == "1", "kopicode should have run exactly once so far"

    # -- Phase 2: a fresh store resumes the same run. -----------------------------
    store = SQLiteStore.open(database)
    resumed = satay.start(_delegate_only, task_input, run_id="crash-test-1", store=store)
    outcome = await resumed.result()
    store.close()

    assert counter_file.read_text().strip() == "1", (
        "resuming must reuse the already-completed delegation, not invoke kopicode again"
    )
    assert outcome.kind == "failed"  # the real, recorded outcome: the provider rejected the key
    episodic_store.close()


@pytest.mark.requires_kopicode
async def test_full_workflow_resumes_to_the_same_terminal_state_after_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=episodic_store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary="kopicode",
        )
    )
    root = tmp_path / "scratch"
    root.mkdir()
    task_id = "crash-test-2"
    task_input = {"task_id": task_id, "text": "add a .gitignore entry", "root": str(root)}
    database = tmp_path / "satay.db"

    # run_task's first durable call is the TaskSubmitted journal append -- its
    # TaskCompleted is deterministically the first of that type in a fresh run.
    store = SQLiteStore.open(database)
    injector = FaultInjector()
    injector.crash_after("TaskCompleted")
    handle = satay.start(run_task, task_input, run_id=task_id, store=store, injector=injector)
    with pytest.raises(SimulatedCrash):
        await handle.result()
    store.close()

    store = SQLiteStore.open(database)
    resumed = satay.start(run_task, task_input, run_id=task_id, store=store)
    result = await resumed.result()
    store.close()

    assert result["status"] == "failed"
    assert "error" in result

    events = list(episodic_store.read(task_id))
    kinds = [type(event.payload).__name__ for event in events]
    # Exactly one TaskSubmitted -- the interrupted-and-resumed append was reused
    # from the journal, not written a second time.
    assert kinds == ["TaskSubmitted", "DelegationStarted", "DelegationFailed", "TaskFailed"]
    assert isinstance(events[0].payload, TaskSubmitted)
    assert isinstance(events[1].payload, DelegationStarted)
    assert isinstance(events[2].payload, DelegationFailed)
    assert isinstance(events[3].payload, TaskFailed)
    episodic_store.close()
