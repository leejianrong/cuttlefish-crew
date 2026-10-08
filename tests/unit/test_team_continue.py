"""Unit/integration: a round that runs out of turns or tokens is a checkpoint, and the team
carries on from the handover with a fresh session (ADR-0030). Real satay, real journal, the
scripted fake ``kopicode serve``; the wrapper plays a different scenario for each session."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from satay.api.primitives import start
from satay.journal.store import SQLiteStore

from cuttlefish import runtime
from cuttlefish.episodic.events import DelegationFailed, DelegationStarted, RoundContinued
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.llm.replay import ReplayLlmProvider
from cuttlefish.requests import RequestBroker, RequestContext
from cuttlefish.team import run_team

FAKE = Path(__file__).parent / "delegate" / "fake_kopicode_serve.py"
FEATURES = ["session.limits", "consent_timeout.flag", "consent_mode.remote_interactive"]


def _event(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "session.event",
        "params": {"session": "$session", "event": body},
    }


def _refusal(command: str) -> list[Any]:
    """A command the agent tried and the gate refused: what makes a round ``refused``."""
    detail = json.dumps({"command": command})
    return [
        {"emit": _event({"kind": "tool_call_parsed", "tool": "run_shell", "detail": detail})},
        {"emit": _event({"kind": "permission_decided", "decision": "deny", "reason": "denied"})},
        {"emit": _event({"kind": "tool_result", "tool": "run_shell", "reason": "denied"})},
    ]


def _round(stop: str, exit_code: int, edit: bool = False, refused: str | None = None) -> list[Any]:
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
    edited = [{"emit": _event({"kind": "edit_applied", "path": "a.py"})}] if edit else []
    denied = _refusal(refused) if refused else []
    return [{"start": True}, *edited, *denied, {"emit": result}, {"close": [ended]}]


def _script(tmp_path: Path, rounds: list[Any], features: list[str] = FEATURES) -> str:
    """A binary that plays ``rounds`` (steps) in one process: the pool keeps one child for the
    whole team."""
    (tmp_path / "scenario.json").write_text(json.dumps([*rounds, {"eof": []}]))
    binary = tmp_path / "kopicode"
    binary.write_text(
        f"""#!/bin/sh
if [ "$1" = version ]; then echo '{json.dumps({"features": features})}'; exit 0; fi
if [ "$2" = --help ]; then echo "--consent-timeout"; exit 0; fi
FAKE_KOPICODE_SCENARIO={tmp_path}/scenario.json FAKE_KOPICODE_LOG={tmp_path}/sent.jsonl \\
  exec {sys.executable} {FAKE} "$@"
"""
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return str(binary)


def _wrapper(
    tmp_path: Path, *, stops: int, stop: str = "max_turns", features: list[str] = FEATURES
) -> str:
    """A binary whose first ``stops`` sessions end on ``stop`` and the next one completes."""
    code = 4 if stop == "max_turns" else 1
    rounds = [s for _ in range(stops) for s in _round(stop, code)]
    return _script(tmp_path, [*rounds, *_round("completed", 0)], features)


def _sent(tmp_path: Path) -> list[dict[str, Any]]:
    lines = (tmp_path / "sent.jsonl").read_text().splitlines()
    return [m for m in map(json.loads, lines) if m.get("method") == "session.start"]


async def _run(tmp_path: Path, binary: str, **team: Any) -> tuple[dict[str, Any], list[Any]]:
    store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=store, llm_provider=ReplayLlmProvider([]), kopicode_binary=binary
        )
    )
    root = tmp_path / "root"
    root.mkdir()
    result = await start(
        run_team,
        {
            "team_id": "t",
            "root": str(root),
            "roles": [{"name": "builder", "text": "build it"}],
            **team,
        },
        run_id="t",
        store=SQLiteStore.open(":memory:"),
    ).result()
    payloads = [e.payload for e in store.read("t")]
    store.close()
    return result, payloads


@pytest.fixture(autouse=True)
def _scratch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CUTTLEFISH_MAX_CONTINUATIONS",
        "CUTTLEFISH_MAX_TURNS",
        "CUTTLEFISH_SESSION_TOKEN_BUDGET",
    ):
        monkeypatch.delenv(name, raising=False)


async def test_a_turn_limit_is_a_checkpoint_and_the_next_round_continues(tmp_path: Path) -> None:
    result, payloads = await _run(tmp_path, _wrapper(tmp_path, stops=1))
    assert result["status"] == "completed"
    continued = [p for p in payloads if isinstance(p, RoundContinued)]
    assert [(c.reason, c.count, c.limit, c.role) for c in continued] == [
        ("max_turns", 1, 20, "builder")
    ]
    assert len([p for p in payloads if isinstance(p, DelegationStarted)]) == 2
    first, second = _sent(tmp_path)
    assert first["params"]["max_turns"] == 100 and first["params"]["token_budget"] == 5_000_000
    assert "continued automatically" in second["params"]["prompt"]
    assert "turn limit" in second["params"]["prompt"]
    assert second["params"]["prompt"] != first["params"]["prompt"]


async def test_a_token_budget_stop_continues_too(tmp_path: Path) -> None:
    result, payloads = await _run(tmp_path, _wrapper(tmp_path, stops=1, stop="budget_exhausted"))
    assert result["status"] == "completed"
    assert [p.reason for p in payloads if isinstance(p, RoundContinued)] == ["budget_exhausted"]


async def test_continuing_is_bounded_and_the_last_stop_fails_the_role(tmp_path: Path) -> None:
    result, payloads = await _run(
        tmp_path, _wrapper(tmp_path, stops=99), max_continuations=2, max_idle_rounds=0
    )
    assert result["status"] == "failed"
    assert [p.count for p in payloads if isinstance(p, RoundContinued)] == [1, 2]
    failures = [p for p in payloads if isinstance(p, DelegationFailed)]
    assert len(failures) == 3 and failures[-1].failure_kind == "max_turns"
    assert "continued 2 of 2 times" in result["roles"]["builder"]["error"]
    assert "CUTTLEFISH_MAX_CONTINUATIONS" in result["roles"]["builder"]["error"]


async def test_zero_continuations_keeps_a_checkpoint_stop_a_failed_round(tmp_path: Path) -> None:
    result, payloads = await _run(tmp_path, _wrapper(tmp_path, stops=1), max_continuations=0)
    assert result["status"] == "failed"
    assert not [p for p in payloads if isinstance(p, RoundContinued)]


async def test_an_older_kopicode_gets_no_limits_but_still_continues(tmp_path: Path) -> None:
    binary = _wrapper(tmp_path, stops=1, features=["consent_timeout.flag"])
    result, _ = await _run(tmp_path, binary)
    assert result["status"] == "completed"
    assert all("max_turns" not in m["params"] for m in _sent(tmp_path))


def _turns(*edits: bool) -> list[Any]:
    return [step for edit in edits for step in _round("max_turns", 4, edit)]


async def test_rounds_that_change_nothing_hold_the_role_instead_of_going_round_in_circles(
    tmp_path: Path,
) -> None:
    result, payloads = await _run(tmp_path, _wrapper(tmp_path, stops=99))
    error = result["roles"]["builder"]["error"]
    assert result["status"] == "failed"
    assert "changed no file in 3 rounds in a row" in error and "CUTTLEFISH_MAX_IDLE_ROUNDS" in error
    assert [p.count for p in payloads if isinstance(p, RoundContinued)] == [1, 2]
    assert len([p for p in payloads if isinstance(p, DelegationStarted)]) == 3


async def test_a_round_that_changed_a_file_starts_the_count_again(tmp_path: Path) -> None:
    binary = _script(tmp_path, [*_turns(False, False, True, False, False), *_round("completed", 0)])
    result, payloads = await _run(tmp_path, binary)
    assert result["status"] == "completed"
    assert len([p for p in payloads if isinstance(p, RoundContinued)]) == 5


async def test_zero_idle_rounds_turns_the_no_progress_stop_off(tmp_path: Path) -> None:
    binary = _script(tmp_path, [*_turns(*[False] * 5), *_round("completed", 0)])
    result, _ = await _run(tmp_path, binary, max_idle_rounds=0)
    assert result["status"] == "completed"


async def test_a_held_role_raises_a_card_that_says_nothing_is_waiting(tmp_path: Path) -> None:
    store = EpisodicStore.open(tmp_path / "episodic.db")
    broker = RequestBroker(store.append)
    runtime.configure(
        runtime.Runtime(
            episodic_store=store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary=_wrapper(tmp_path, stops=99),
            requests=RequestContext(broker, "p1", "t", 600.0),
        )
    )
    root = tmp_path / "root"
    root.mkdir()
    await start(
        run_team,
        {"team_id": "t", "root": str(root), "roles": [{"name": "builder", "text": "go"}]},
        run_id="t",
        store=SQLiteStore.open(":memory:"),
    ).result()
    (card,) = broker.pending()
    assert card.record.kind == "blocked" and card.record.role == "builder"
    assert card.record.title == "{who} has gone round in circles".format(who="builder")
    assert "3 rounds in a row" in card.record.detail
    store.close()


async def test_a_refused_command_does_not_end_the_role_and_the_next_round_is_told_which(
    tmp_path: Path,
) -> None:
    binary = _script(
        tmp_path,
        [*_round("completed", 0, refused="cd /testbed && git status"), *_round("completed", 0)],
    )
    result, payloads = await _run(tmp_path, binary)
    assert result["status"] == "completed"
    assert [p.reason for p in payloads if isinstance(p, RoundContinued)] == ["refused"]
    second = _sent(tmp_path)[1]["params"]["prompt"]
    assert "cd /testbed && git status" in second and "refused" in second
    assert "do not cd elsewhere" in second


async def test_refusals_that_never_stop_hold_the_role_like_any_other_going_round_in_circles(
    tmp_path: Path,
) -> None:
    rounds = [s for _ in range(5) for s in _round("completed", 0, refused="rm -rf /")]
    result, payloads = await _run(tmp_path, _script(tmp_path, rounds))
    assert result["status"] == "failed"
    assert "changed no file in 3 rounds in a row" in result["roles"]["builder"]["error"]
    assert len([p for p in payloads if isinstance(p, DelegationStarted)]) == 3


USAGE_FEATURES = [*FEATURES, "session.usage", "usage.context", "usage.context_window"]


def _full_context_round() -> list[Any]:
    """A round whose context reaches 90% of a 100k window: cuttlefish cancels it."""
    cancelled = _event({"kind": "session_ended", "reason": "cancelled", "exit_code": 1})
    result = _round("cancelled", 1)[-2]
    return [
        {"start": True},
        {"emit": _event({"kind": "edit_applied", "path": "a.py"})},
        {"emit": _event({"kind": "provider_response", "size": 9})},
        {"usage": {"context_tokens": 90_000, "context_window": 100_000}},
        {"wait_for": "session.cancel"},
        result,
        {"close": [cancelled]},
    ]


async def test_a_round_that_fills_the_context_is_a_checkpoint_and_the_next_round_continues(
    tmp_path: Path,
) -> None:
    binary = _script(tmp_path, [*_full_context_round(), *_round("completed", 0)], USAGE_FEATURES)
    result, payloads = await _run(tmp_path, binary)
    assert result["status"] == "completed"
    assert [p.reason for p in payloads if isinstance(p, RoundContinued)] == ["context_pressure"]
    assert "context was nearly full" in _sent(tmp_path)[1]["params"]["prompt"]


async def test_a_kopicode_that_cannot_report_usage_is_never_asked(tmp_path: Path) -> None:
    binary = _wrapper(tmp_path, stops=0)
    await _run(tmp_path, binary)
    lines = (tmp_path / "sent.jsonl").read_text()
    assert "session.usage" not in lines
