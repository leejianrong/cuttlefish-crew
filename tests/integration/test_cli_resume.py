"""KAN-1806: the CLI's resume/warn path over a real, crashed satay run."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import satay
from satay.config import db_path
from satay.journal.store import SQLiteStore
from satay.testing import FaultInjector, SimulatedCrash

from cuttlefish import resume
from cuttlefish.cli import EXIT_CONFIG_ERROR, EXIT_OK, _resolve_run_id
from cuttlefish.episodic.events import TeamResumed
from cuttlefish.episodic.store import EpisodicStore


@satay.task()
async def _noop() -> str:
    return "ok"


@satay.workflow
async def _wf(arg: dict[str, str]) -> str:
    return await _noop()


async def _crash_a_run(run_id: str) -> None:
    database = db_path(Path.cwd() / ".satay")
    database.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStore.open(database)
    injector = FaultInjector()
    injector.crash_after("TaskCompleted")
    handle = satay.start(_wf, {"a": "b"}, run_id=run_id, store=store, injector=injector)
    with pytest.raises(SimulatedCrash):
        await handle.result()
    store.close()


def _args(resume_id: str | None) -> argparse.Namespace:
    return argparse.Namespace(resume=resume_id)


async def test_a_crashed_run_is_found_warned_about_and_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SATAY_DATA_DIR", raising=False)
    await _crash_a_run("crashed-1")

    assert await resume.unfinished_runs() == ["crashed-1"]

    # A plain rerun warns, naming the id, and still mints a new one.
    run_id, code = await _resolve_run_id(_args(None), "run")
    assert code == EXIT_OK and run_id != "crashed-1"
    assert "crashed-1" in capsys.readouterr().err

    # --resume reuses the id and states the in-flight-round caveat.
    run_id, code = await _resolve_run_id(_args("crashed-1"), "run")
    assert (run_id, code) == ("crashed-1", EXIT_OK)
    assert "starts over" in capsys.readouterr().err

    # The same satay primitive then actually finishes it.
    store = SQLiteStore.open(db_path(tmp_path / ".satay"))
    result = await satay.start(_wf, {"a": "b"}, run_id="crashed-1", store=store).result()
    store.close()
    assert result == "ok"
    assert await resume.unfinished_runs() == []

    # Finished runs can't be resumed again.
    run_id, code = await _resolve_run_id(_args("crashed-1"), "run")
    assert (run_id, code) == (None, EXIT_CONFIG_ERROR)


async def test_resuming_an_unknown_id_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SATAY_DATA_DIR", raising=False)
    assert await _resolve_run_id(_args("nope"), "run") == (None, EXIT_CONFIG_ERROR)
    assert "no run 'nope'" in capsys.readouterr().err


def test_mark_resumed_journals_the_last_seq(tmp_path: Path) -> None:
    store = EpisodicStore.open(tmp_path / "e.db")
    store.append("t", TeamResumed(resumed_from_seq=0))
    resume.mark_resumed(store, "t")
    seqs = [e.seq for e in store.read("t")]
    last = list(store.read("t"))[-1].payload
    assert isinstance(last, TeamResumed) and last.resumed_from_seq == seqs[-2]
    store.close()
