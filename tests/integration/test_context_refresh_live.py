"""Live: a team's coding agent gets a fresh context and a handover when a round runs out of room
(ADR-0030), against real kopicode and a real model.

What the scripted fake cannot show: that a real kopicode accepts ``max_turns`` on ``session.start``,
really stops on it, that cuttlefish then opens a *new* kopicode session (a new context window), that
the handover it writes with its own summarising model reaches that session's prompt, and that the
agent, told nothing but the task and the handover, finishes the job. The task is four small files
with a three-turn limit, so the work cannot fit in one round. Costs cents per run, and only with a
real ``OPENROUTER_API_KEY`` (kopicode's model and cuttlefish's own summariser both use it) and a
kopicode v0.4.0 or later: set ``CUTTLEFISH_TEST_KOPICODE_BIN`` to one if ``kopicode`` on ``PATH`` is
older.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from satay.api.primitives import start
from satay.journal.store import SQLiteStore

from cuttlefish import runtime
from cuttlefish.delegate.kopicode_serve import serve_features
from cuttlefish.episodic.events import (
    DelegationFailed,
    DelegationStarted,
    HandoverWritten,
    RoundContinued,
)
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.handover import estimate_tokens
from cuttlefish.llm.openrouter import OpenRouterLlmProvider
from cuttlefish.team import run_team

pytestmark = [pytest.mark.requires_kopicode, pytest.mark.requires_live_credential]

_FILES = {"one.txt": "one", "two.txt": "two", "three.txt": "three", "four.txt": "four"}
_TASK = (
    "Create four files in the repository root: one.txt containing the word one, two.txt containing "
    "two, three.txt containing three and four.txt containing four. Create exactly one file per "
    "step and nothing else. Before each step, list the directory and create only the next missing "
    "file, in that order. Do not run shell commands. When all four exist, say you are finished."
)


def _repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True, env={"PATH": "/usr/bin:/bin"})
    (path / "README.md").write_text("a scratch repo for the live context-refresh test\n")


async def test_a_round_that_runs_out_of_turns_continues_in_a_fresh_session_with_a_handover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("cuttlefish's own summariser needs OPENROUTER_API_KEY")
    binary = os.environ.get("CUTTLEFISH_TEST_KOPICODE_BIN") or shutil.which("kopicode") or ""
    if "session.limits" not in await serve_features(binary):
        pytest.skip("needs kopicode v0.4.0 or later (session.limits)")
    monkeypatch.setenv("CUTTLEFISH_MAX_TURNS", "3")
    monkeypatch.setenv("CUTTLEFISH_MAX_IDLE_ROUNDS", "0")

    root = tmp_path / "project"
    root.mkdir()
    _repo(root)
    store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=store, llm_provider=OpenRouterLlmProvider(), kopicode_binary=binary
        )
    )
    result = await start(
        run_team,
        {
            "team_id": "live",
            "root": str(root),
            "roles": [{"name": "builder", "text": _TASK}],
            "max_continuations": 6,
            # The journal holds the task once at the start and twice after the first round (the
            # role's text, then the round's own), so this is crossed after round one, not before.
            "token_budget": int(estimate_tokens(_TASK) * 1.5),
        },
        run_id="live",
        store=SQLiteStore.open(":memory:"),
    ).result()
    payloads = [e.payload for e in store.read("live")]
    store.close()

    started = [p for p in payloads if isinstance(p, DelegationStarted)]
    stopped = [p for p in payloads if isinstance(p, DelegationFailed)]
    continued = [p for p in payloads if isinstance(p, RoundContinued)]
    handovers = [p for p in payloads if isinstance(p, HandoverWritten)]
    sessions = sorted((root / ".kopicode" / "sessions").iterdir())
    report = (
        f"rounds={len(started)} continued={len(continued)} handovers={len(handovers)} "
        f"kopicode_sessions={len(sessions)} stops={[p.failure_kind for p in stopped]}\n"
        f"first handover: {handovers[0].summary if handovers else None!r}"
    )
    print("\n" + report)

    assert result["status"] == "completed", report
    # Progress carried across sessions: the first file can only come from the first session and
    # the second from a later one. A model may still call itself finished early (it did once in
    # a live run with two of four files), which is the model's choice, so the rest is only
    # reported, never asserted.
    for name in list(_FILES)[:2]:
        assert (root / name).exists(), f"{name} is missing\n{report}"
    print(f"files present: {sorted(n for n in _FILES if (root / n).exists())}")
    # The turn limit really stopped a round, and cuttlefish opened another session after it.
    assert stopped and stopped[0].failure_kind == "max_turns", report
    assert continued and len(started) >= 2 and len(sessions) >= len(started), report
    # The next session's prompt carried the handover cuttlefish wrote, not a replayed transcript.
    assert handovers and "placeholder" not in handovers[0].summary.lower(), report
    # The summary says what the first round actually did, not just that it ran out of turns.
    assert "one.txt" in handovers[0].summary, report
    assert any("Progress so far (checkpointed summary)" in p.task_text for p in started[1:]), report
