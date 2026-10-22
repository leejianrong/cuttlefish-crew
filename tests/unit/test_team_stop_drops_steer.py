"""A steer still queued when Stop is pressed is dropped, not applied as one more round (CUT-107).

Real satay, real journal, the scripted fake ``kopicode serve``. The daemon journals
``StopRequested`` when Stop is pressed; the workflow reads it at the round boundary, before it
would consume the queued steer."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any

import satay
from satay.journal.store import SQLiteStore

from cuttlefish import runtime
from cuttlefish.episodic.events import (
    DelegationFailed,
    DelegationStarted,
    SteeringMessage,
    StopRequested,
    TaskCompleted,
    TaskFailed,
)
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.steering import steering_key
from cuttlefish.team import run_team

FAKE = Path(__file__).parent / "delegate" / "fake_kopicode_serve.py"
FEATURES = ["session.limits", "consent_timeout.flag", "consent_mode.remote_interactive"]


def _event(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "session.event",
        "params": {"session": "$session", "event": body},
    }


def _completed_round(stop: str = "completed", exit_code: int = 0) -> list[Any]:
    result = {
        "jsonrpc": "2.0",
        "id": "$start_id",
        "result": {
            "session": "$session",
            "record": "/r",
            "stop": stop,
            "exit_code": exit_code,
            "turns": 1,
        },
    }
    ended = _event({"kind": "session_ended", "reason": stop, "exit_code": exit_code})
    return [{"start": True}, {"emit": result}, {"close": [ended]}]


def _binary(tmp_path: Path, *, first: list[Any] | None = None) -> str:
    """A kopicode whose every session completes at once; two are scripted so a second round,
    were one started, would run rather than fail."""
    rounds = [*(first or _completed_round()), *_completed_round(), {"eof": []}]
    (tmp_path / "scenario.json").write_text(json.dumps(rounds))
    binary = tmp_path / "kopicode"
    binary.write_text(
        f"""#!/bin/sh
if [ "$1" = version ]; then echo '{json.dumps({"features": FEATURES})}'; exit 0; fi
if [ "$2" = --help ]; then echo "--consent-timeout"; exit 0; fi
FAKE_KOPICODE_SCENARIO={tmp_path}/scenario.json FAKE_KOPICODE_LOG={tmp_path}/sent.jsonl \\
  exec {sys.executable} {FAKE} "$@"
"""
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return str(binary)


async def _run_with_queued_steer(
    tmp_path: Path, *, stop_pressed: bool, first: list[Any] | None = None
) -> list[Any]:
    store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=store,
            kopicode_binary=_binary(tmp_path, first=first),
        )
    )
    root = tmp_path / "root"
    root.mkdir()
    team_id = "t"
    if stop_pressed:
        store.append(team_id, StopRequested())
    async with satay.run_app(store=SQLiteStore.open(":memory:")) as satay_store:
        handle = satay.start(
            run_team,
            {
                "team_id": team_id,
                "root": str(root),
                "roles": [{"name": "builder", "text": "build it"}],
                "steerable": True,
                "steering_grace": 0.3,
            },
            run_id=team_id,
            store=satay_store,
        )
        await satay.send_event(
            SteeringMessage(text="now do something else", role="builder"),
            key=steering_key(team_id, "builder"),
            store=satay_store,
        )
        await handle.result()
    payloads = [event.payload for event in store.read(team_id)]
    store.close()
    runtime.reset()
    return payloads


async def test_without_a_stop_the_queued_steer_starts_a_second_round(tmp_path: Path) -> None:
    payloads = await _run_with_queued_steer(tmp_path, stop_pressed=False)

    assert len([p for p in payloads if isinstance(p, DelegationStarted)]) == 2
    assert any(isinstance(p, SteeringMessage) for p in payloads)


async def test_a_steer_queued_when_stop_was_pressed_is_dropped_and_no_round_follows(
    tmp_path: Path,
) -> None:
    payloads = await _run_with_queued_steer(tmp_path, stop_pressed=True)

    assert len([p for p in payloads if isinstance(p, DelegationStarted)]) == 1
    assert not any(isinstance(p, SteeringMessage) for p in payloads)
    assert any(isinstance(p, TaskCompleted) for p in payloads)


async def test_a_round_cancelled_by_stop_leaves_the_role_open_so_the_team_reads_it_as_stopped(
    tmp_path: Path,
) -> None:
    # CUT-166: Codex's interrupted turn is a failed, cancelled round. Writing TaskFailed for it
    # would pin the role as failed; without one, TeamStopped marks it stopped.
    payloads = await _run_with_queued_steer(
        tmp_path, stop_pressed=True, first=_completed_round("cancelled", 130)
    )

    failed = [p for p in payloads if isinstance(p, DelegationFailed)]
    assert len(failed) == 1 and failed[0].failure_kind == "cancelled"
    assert not any(isinstance(p, TaskFailed | TaskCompleted) for p in payloads)
