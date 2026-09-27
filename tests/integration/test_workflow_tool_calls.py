"""Integration: `run_task` journals a `ToolCallRecorded` event per individual
tool call a real delegation made (KAN-1714/ADR-0019), against a real model --
gated the identical way `test_kopicode_live.py` already is (a real API call,
`requires_live_credential`), for the identical reason: `classify_stream`'s own
unit tests only assert against a synthetic event shape, and this project's
standing discipline is to also prove the real shape at least once, live, not
just reason about it (docs/PLAN.md "Testing approach").
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import satay
from satay.journal.store import SQLiteStore

from cuttlefish import runtime
from cuttlefish.episodic.events import ToolCallRecorded
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.llm.replay import ReplayLlmProvider
from cuttlefish.workflow import run_task


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    yield
    runtime.reset()


def _configure_runtime(episodic_store: EpisodicStore) -> None:
    runtime.configure(
        runtime.Runtime(
            episodic_store=episodic_store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary="kopicode",
        )
    )


def _init_scratch_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "cuttlefish-tests"], cwd=root, check=True)
    (root / "README.md").write_text("a scratch checkout for a live tool-call-tracing test\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.mark.requires_kopicode
@pytest.mark.requires_live_credential
async def test_a_real_delegation_journals_one_tool_call_recorded_per_call(
    tmp_path: Path,
) -> None:
    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    _configure_runtime(episodic_store)
    task_id = "tool-call-tracing-task"
    root = tmp_path / "scratch"
    root.mkdir()
    _init_scratch_repo(root)

    async with satay.run_app(store=SQLiteStore.open(":memory:")) as store:
        handle = satay.start(
            run_task,
            {
                "task_id": task_id,
                "text": (
                    "Create a file named LIVE_TOOL_CALL_TEST.txt containing the single "
                    "line: cuttlefish live test"
                ),
                "root": str(root),
            },
            run_id=task_id,
            store=store,
        )
        result = await handle.result()

    assert result["status"] == "completed"

    events = list(episodic_store.read(task_id))
    tool_call_events = [e.payload for e in events if isinstance(e.payload, ToolCallRecorded)]
    assert len(tool_call_events) >= 1
    assert all(call.status in ("ok", "denied", "error") for call in tool_call_events)
    assert any(call.status == "ok" for call in tool_call_events)

    episodic_store.close()
