"""Live: a real model's off-list command is held for a person and the answer reaches kopicode
(ADR-0028, V4-H5).

The thing the stub child cannot show: a real model calls ``run_shell``, kopicode sends a real
``consent.request``, cuttlefish raises a request instead of answering, and the person's answer
-- through the broker, the same call the HTTP route makes -- lands in kopicode's own journal
as a ``permission_decided`` with ``source: remote``. The role may run ``ls`` and nothing else,
so ``echo`` is off the list: one is allowed once, the other denied. Costs cents per run, and
only with a real key in the environment (``requires_live_credential``). Needs a kopicode with
``serve --consent-timeout`` (v0.3.0 or main) for the second test.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cuttlefish.agents.outcome import DelegationOutcome
from cuttlefish.delegate.kopicode_serve import run_kopicode_serve, serve_supports_consent_timeout
from cuttlefish.episodic.events import EventPayload, RequestRaised, RequestResolved
from cuttlefish.episodic.store import EpisodicEvent
from cuttlefish.requests import AskingDecider, PendingRequest, RequestBroker, RequestContext

_TASK = (
    "Use the run_shell tool to run exactly this command: echo first\n"
    "Then use the run_shell tool to run exactly this command: echo second\n"
    "Run both even if one is refused. Do not edit any file. Then say you are finished."
)


class _Journal:
    def __init__(self) -> None:
        self.payloads: list[EventPayload] = []

    def append(self, task_id: str, payload: EventPayload) -> EpisodicEvent:
        self.payloads.append(payload)
        return EpisodicEvent(task_id, len(self.payloads), 1, datetime.now(UTC), payload)


def _repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True, env={"PATH": "/usr/bin:/bin"})
    (path / "README.md").write_text("a scratch repo for the live Needs-you test\n")


async def _answer_each(
    broker: RequestBroker, *, delay: float, allow_line: str, seen: list[PendingRequest]
) -> None:
    """Play the person: after ``delay`` seconds, allow ``allow_line`` once and deny the rest."""
    answered: set[str] = set()
    while True:
        for request in broker.pending():
            if request.id in answered:
                continue
            answered.add(request.id)
            seen.append(request)
            await asyncio.sleep(delay)
            allowed = request.record.detail == allow_line
            broker.answer(request.id, "allow_once" if allowed else "deny")
        await asyncio.sleep(0.1)


async def _run(
    tmp_path: Path, *, delay: float, window_s: float
) -> tuple[DelegationOutcome, _Journal, list[PendingRequest]]:
    _repo(tmp_path)
    journal = _Journal()
    broker = RequestBroker(journal.append)
    asker = RequestContext(broker, "p1", "t1", window_s).asker(role="builder", backend="kopicode")
    decider = AskingDecider([["ls"]], asker, window_s=window_s)
    seen: list[PendingRequest] = []
    person = asyncio.create_task(
        _answer_each(broker, delay=delay, allow_line="echo first", seen=seen)
    )
    try:
        outcome = await run_kopicode_serve(
            binary="kopicode",
            task_text=_TASK,
            root=str(tmp_path),
            policy=decider,
            env={"OPENROUTER_API_KEY": os.environ["OPENROUTER_API_KEY"]},
            timeout=window_s * 3 + 300,
            consent_timeout=window_s + 30,
        )
    finally:
        person.cancel()
    return outcome, journal, seen


def _assert_the_answers_reached_kopicode(
    outcome: DelegationOutcome, journal: _Journal, seen: list[PendingRequest]
) -> None:
    raised = [p for p in journal.payloads if isinstance(p, RequestRaised)]
    resolved = {p.request_id: p for p in journal.payloads if isinstance(p, RequestResolved)}
    by_line = {r.detail: resolved[r.request_id].resolution for r in raised}
    assert by_line.get("echo first") == "allowed_once", by_line
    assert by_line.get("echo second") == "denied", by_line
    assert all(r.role == "builder" for r in raised)
    # kopicode's own journal agrees: its remote-sourced decisions became tool-call statuses.
    statuses = {c.detail: c.status for c in outcome.tool_calls if c.tool == "run_shell"}
    assert any("echo first" in d and s == "ok" for d, s in statuses.items()), outcome.tool_calls
    assert any("echo second" in d and s == "denied" for d, s in statuses.items()), (
        outcome.tool_calls
    )


@pytest.mark.requires_kopicode
@pytest.mark.requires_live_credential
async def test_a_real_model_is_held_for_a_person_and_the_answers_reach_kopicode(
    tmp_path: Path,
) -> None:
    outcome, journal, seen = await _run(tmp_path, delay=2.0, window_s=40.0)
    _assert_the_answers_reached_kopicode(outcome, journal, seen)


@pytest.mark.requires_kopicode
@pytest.mark.requires_live_credential
async def test_a_request_is_still_answerable_after_kopicodes_default_sixty_seconds(
    tmp_path: Path,
) -> None:
    """The point of ``--consent-timeout``: at kopicode's own 60s default the held request would
    already be denied and the late answer dropped. Needs the flag (v0.3.0 or main)."""
    if not await serve_supports_consent_timeout("kopicode"):
        pytest.skip("this kopicode has no --consent-timeout (before v0.3.0)")
    outcome, journal, seen = await _run(tmp_path, delay=65.0, window_s=120.0)
    _assert_the_answers_reached_kopicode(outcome, journal, seen)
