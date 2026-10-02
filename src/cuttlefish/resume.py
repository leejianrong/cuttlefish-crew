"""CLI-side resume for ``cuttlefish run``/``run-team`` (KAN-1806, ADR-0010).

The one-shot CLI used to always mint a fresh id, so a crashed run's row stayed
non-terminal in ``.satay/`` and a rerun silently started something unrelated.
satay's own ``satay.start(..., run_id=)`` already resumes a non-terminal run; this
module only finds such runs (read-only, WAL-safe) and journals the same
``TeamResumed`` marker the daemon writes, so ``cuttlefish show`` can see it.

Resume re-drives the workflow with the *same* id, so the operator must repeat the
original command's arguments: satay replays against the input it is given.
"""

from __future__ import annotations

from pathlib import Path

from satay.config import db_path as satay_db_path
from satay.config import resolve_data_dir
from satay.journal.events import TERMINAL_STATUSES
from satay.journal.store import SQLiteStore

from cuttlefish.episodic.events import TeamResumed
from cuttlefish.episodic.store import EpisodicStore

#: Printed on every resume: the round that was in flight when the process died
#: re-runs from its start (ADR-0010 item 2), possibly against an already-edited tree.
RESUME_CAVEAT = (
    "the delegation round that was in flight when the run died starts over, "
    "possibly against a working tree it already edited; finished rounds are not re-run"
)


async def run_status(run_id: str, data_dir: Path | None = None) -> str | None:
    """`run_id`'s satay status in the current data dir, or `None` if unknown."""
    database = satay_db_path(resolve_data_dir(data_dir))
    if not database.exists():
        return None
    store = SQLiteStore.open(database)
    try:
        record = await store.get_run(run_id)
    finally:
        store.close()
    return None if record is None else str(record.status)


async def unfinished_runs(data_dir: Path | None = None) -> list[str]:
    """Ids of every non-terminal run in the current data dir, oldest first. A run
    still live in another process is indistinguishable from a crashed one here."""
    database = satay_db_path(resolve_data_dir(data_dir))
    if not database.exists():
        return []
    store = SQLiteStore.open(database)
    try:
        found: list[str] = []
        for run_id in await store.list_runs():
            record = await store.get_run(run_id)
            if record is not None and record.status not in TERMINAL_STATUSES:
                found.append(run_id)
        return found
    finally:
        store.close()


def mark_resumed(store: EpisodicStore, run_id: str) -> None:
    last_seq = 0
    for event in store.read(run_id):
        last_seq = event.seq
    store.append(run_id, TeamResumed(resumed_from_seq=last_seq))
