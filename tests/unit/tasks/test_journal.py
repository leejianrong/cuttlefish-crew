"""Called outside a workflow drive, a @satay.task simply executes (satay's own
decorators.py doc comment) -- so these are unit-testable directly, no workflow needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cuttlefish import runtime
from cuttlefish.episodic.events import TaskCompleted, TaskSubmitted
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.tasks.journal import journal


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    yield
    runtime.reset()


async def test_journal_appends_the_encoded_payload(tmp_path: Path) -> None:
    store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(runtime.Runtime(episodic_store=store, kopicode_binary="kopicode"))

    await journal("task-1", TaskSubmitted(text="hello"))
    await journal("task-1", TaskCompleted(result="done"))

    events = list(store.read("task-1"))
    assert [event.payload for event in events] == [
        TaskSubmitted(text="hello"),
        TaskCompleted(result="done"),
    ]
    store.close()
