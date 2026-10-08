"""Unit: the kopicode serve transport, against a scripted fake child (stdin/stdout).

The fake (``fake_kopicode_serve.py``) speaks kopicode's serve wire from a JSON scenario and
logs every line the client sends, so each test asserts on what the client *sent* as well as
the outcome it returned.
"""

from __future__ import annotations

import asyncio
import json
import logging
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from cuttlefish.agents.outcome import DelegationError
from cuttlefish.delegate import kopicode_serve
from cuttlefish.delegate.consent import ConsentDecision, ConsentPolicy
from cuttlefish.delegate.kopicode_serve import (
    ConsentRecord,
    ServePool,
    failure_kind_for,
    run_kopicode_serve,
)
from cuttlefish.sandbox.provider import SandboxError

FAKE = Path(__file__).with_name("fake_kopicode_serve.py")
POLICY = ConsentPolicy([["uv", "run", "pytest"]])
KEY = "sk-or-v1-" + "0" * 40
#: what kopicode sends as a run_shell consent `detail`: the argv it will run, space-joined.
SH = "/bin/sh -c "


def event(session_event: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "session.event",
        "params": {"session": "$session", "event": session_event},
    }


def respond(stop: str = "completed", exit_code: int = 0) -> dict[str, Any]:
    return {
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


def ended(reason: str = "completed", exit_code: int = 0, text: str = "") -> dict[str, Any]:
    body: dict[str, Any] = {"kind": "session_ended", "reason": reason, "exit_code": exit_code}
    if text:
        body["text"] = text
    return event(body)


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[list[Any]], str]:
    """Install a scenario; returns the fake binary's path. `fake.sent()` reads the log."""
    log = tmp_path / "sent.jsonl"
    monkeypatch.setenv("FAKE_KOPICODE_LOG", str(log))

    def install(scenario: list[Any]) -> str:
        path = tmp_path / "scenario.json"
        path.write_text(json.dumps(scenario))
        monkeypatch.setenv("FAKE_KOPICODE_SCENARIO", str(path))
        binary = tmp_path / "kopicode"
        binary.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n')
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        return str(binary)

    return install


def sent(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "sent.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


async def run(binary: str, tmp_path: Path, **kwargs: Any) -> Any:
    kwargs.setdefault("policy", POLICY)
    return await run_kopicode_serve(
        binary=binary, task_text="do it", root=str(tmp_path), timeout=20, **kwargs
    )


async def test_events_and_consent_before_the_start_response_are_demultiplexed(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        [
            {"start": True},
            {"emit": event({"kind": "provider_response", "size": 40})},
            {
                "consent": {
                    "id": "c-1",
                    "kind": "run_shell",
                    "detail": SH + "uv run pytest -q",
                }
            },
            {"emit": event({"kind": "edit_applied", "path": "a.py"})},
            {"emit": event({"kind": "provider_response", "size": 2})},
            {"emit": respond()},
            {"close": [ended()]},
            {"eof": []},
        ]
    )
    outcome = await run(binary, tmp_path)

    assert outcome.kind == "completed"
    assert outcome.edited_paths == ["a.py"]
    assert outcome.tokens == 42
    assert outcome.failure_kind is None
    messages = sent(tmp_path)
    start = next(m for m in messages if m.get("method") == "session.start")
    assert start["params"]["consent_mode"] == "remote_interactive"
    assert set(start["params"]) == {"session", "dir", "prompt", "consent_mode"}
    reply = next(m for m in messages if m.get("id") == "c-1")
    assert reply == {"jsonrpc": "2.0", "id": "c-1", "result": {"answer": "allow"}}


async def test_a_denied_command_is_answered_deny_and_the_outcome_is_refused(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        [
            {"start": True},
            {
                "consent": {
                    "id": "c-1",
                    "kind": "run_shell",
                    "detail": SH + "uv run pytest && rm -rf /",
                }
            },
            {"emit": event({"kind": "permission_decided", "decision": "deny", "reason": "remote"})},
            {"emit": respond()},
            {"close": [ended()]},
            {"eof": []},
        ]
    )
    records: list[ConsentRecord] = []
    outcome = await run(binary, tmp_path, on_consent=records.append)

    assert outcome.kind == "refused"
    reply = next(m for m in sent(tmp_path) if m.get("id") == "c-1")
    assert reply["result"] == {"answer": "deny"}
    assert [(r.answer, r.rule) for r in records] == [("deny", "never_allowed:write_outside_root")]
    assert records[0].detail == SH + "uv run pytest && rm -rf /"


@pytest.mark.parametrize(
    ("extra", "rule"),
    [
        # v0.3.0's exact argv and command agree with detail: decided as ever (ls is allowed below).
        ({"argv": ["/bin/sh", "-c", "ls"], "command": "ls"}, "allow:ls"),
        # an older kopicode sends neither
        ({}, "allow:ls"),
        # an argv that is not exactly /bin/sh -c <line> would read as the line "ls x" in detail
        ({"argv": ["/bin/sh", "-c", "ls", "x"]}, "not_a_sh_c_command"),
        ({"argv": ["/bin/bash", "-c", "ls"], "command": "ls"}, "not_a_sh_c_command"),
        ({"argv": ["/bin/sh", "-c", "ls"], "command": "ls; rm x"}, "not_a_sh_c_command"),
    ],
)
async def test_the_exact_argv_kopicode_sends_must_be_sh_dash_c_a_line(
    fake: Callable[[list[Any]], str],
    tmp_path: Path,
    extra: dict[str, Any],
    rule: str,
) -> None:
    binary = fake(
        [
            {"start": True},
            {"consent": {"id": "c-1", "kind": "run_shell", "detail": SH + "ls", **extra}},
            {"emit": respond()},
            {"close": [ended()]},
            {"eof": []},
        ]
    )
    records: list[ConsentRecord] = []
    await run(binary, tmp_path, on_consent=records.append, policy=ConsentPolicy([["ls"]]))

    assert [r.rule for r in records] == [rule]


async def test_every_consent_decision_is_carried_on_the_outcome_in_order(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        [
            {"start": True},
            {
                "consent": {
                    "id": "c-1",
                    "kind": "run_shell",
                    "detail": SH + "uv run pytest -q",
                }
            },
            {
                "consent": {
                    "id": "c-2",
                    "kind": "run_shell",
                    "detail": SH + "uv run pytest && rm -rf /",
                }
            },
            {"emit": respond()},
            {"close": [ended()]},
            {"eof": []},
        ]
    )
    outcome = await run(binary, tmp_path)

    assert [(d.kind, d.detail, d.answer) for d in outcome.consent_decisions] == [
        ("run_shell", SH + "uv run pytest -q", "allow"),
        ("run_shell", SH + "uv run pytest && rm -rf /", "deny"),
    ]
    assert outcome.consent_decisions[1].rule == "never_allowed:write_outside_root"


@pytest.mark.parametrize(
    "params",
    [
        {"kind": "run_shell", "detail": 7},
        {"kind": None, "detail": "uv run pytest"},
        {"detail": "uv run pytest"},
    ],
)
async def test_a_malformed_consent_request_is_denied_and_the_turn_continues(
    fake: Callable[[list[Any]], str], tmp_path: Path, params: dict[str, Any]
) -> None:
    binary = fake(
        [
            {"start": True},
            {"consent": {"id": "c-9", **params}},
            {"emit": respond()},
            {"close": [ended()]},
            {"eof": []},
        ]
    )
    records: list[ConsentRecord] = []
    outcome = await run(binary, tmp_path, on_consent=records.append)

    assert outcome.kind == "completed"
    reply = next(m for m in sent(tmp_path) if m.get("id") == "c-9")
    assert reply["result"] == {"answer": "deny"}
    assert records[0].rule == "malformed_request"


async def test_a_consent_request_with_no_params_at_all_is_denied(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        [
            {"start": True},
            {"emit": {"jsonrpc": "2.0", "id": "c-2", "method": "consent.request"}},
            {"emit": respond()},
            {"close": [ended()]},
            {"eof": []},
        ]
    )
    await run(binary, tmp_path)
    reply = next(m for m in sent(tmp_path) if m.get("id") == "c-2")
    assert reply["result"] == {"answer": "deny"}


async def test_a_decider_slower_than_the_deadline_is_denied_by_the_client(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    async def hangs(kind: str, detail: str) -> ConsentDecision:
        await asyncio.sleep(30)
        return ConsentDecision("allow", "never")

    binary = fake(
        [
            {"start": True},
            {"consent": {"id": "c-1", "kind": "run_shell", "detail": SH + "uv run pytest"}},
            {"emit": respond()},
            {"close": [ended()]},
            {"eof": []},
        ]
    )
    records: list[ConsentRecord] = []
    await run(binary, tmp_path, policy=hangs, consent_deadline=0.05, on_consent=records.append)

    reply = next(m for m in sent(tmp_path) if m.get("id") == "c-1")
    assert reply["result"] == {"answer": "deny"}
    assert records[0].rule == "decider_timeout"


async def test_a_decider_that_raises_is_denied(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    def boom(kind: str, detail: str) -> ConsentDecision:
        raise RuntimeError("bug")

    binary = fake(
        [
            {"start": True},
            {"consent": {"id": "c-1", "kind": "run_shell", "detail": SH + "x"}},
            {"emit": respond()},
            {"close": [ended()]},
            {"eof": []},
        ]
    )
    await run(binary, tmp_path, policy=boom)
    reply = next(m for m in sent(tmp_path) if m.get("id") == "c-1")
    assert reply["result"] == {"answer": "deny"}


async def test_a_reply_that_loses_to_kopicodes_own_timeout_is_harmless(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    """kopicode denies at 60s and drops a late reply; here the fake gives up after 0.1s
    while the client's decider is still thinking, then the turn finishes as a refusal."""

    async def slow(kind: str, detail: str) -> ConsentDecision:
        await asyncio.sleep(0.4)
        return ConsentDecision("allow", "late")

    binary = fake(
        [
            {"start": True},
            {
                "consent": {"id": "c-1", "kind": "run_shell", "detail": SH + "uv run pytest"},
                "wait": 0.1,
            },
            {
                "emit": event(
                    {"kind": "permission_decided", "decision": "deny", "reason": "timeout"}
                )
            },
            {"emit": respond()},
            {"close": [ended()]},
            {"eof": []},
        ]
    )
    outcome = await run(binary, tmp_path, policy=slow)

    assert outcome.kind == "refused"
    log_lines = sent(tmp_path)
    assert {"reply_seen": "timeout"} in log_lines


async def test_cancelling_while_a_consent_request_is_outstanding_denies_it_then_cancels(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    asking = asyncio.Event()

    async def never_answers(kind: str, detail: str) -> ConsentDecision:
        asking.set()
        await asyncio.sleep(3600)
        return ConsentDecision("allow", "never")

    binary = fake(
        [
            {"start": True},
            {
                "consent": {"id": "c-1", "kind": "run_shell", "detail": SH + "uv run pytest"},
                "wait": 10,
            },
            {"wait_for": "session.cancel"},
            {"emit": respond("cancelled", 1)},
            {"close": [ended("cancelled", 1, "context canceled")]},
            {"eof": []},
        ]
    )
    task = asyncio.create_task(run(binary, tmp_path, policy=never_answers))
    await asyncio.wait_for(asking.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    messages = sent(tmp_path)
    methods = [
        m.get("method") or ("reply:" + str(m["id"]) if "id" in m else "eof" if m.get("eof") else "")
        for m in messages
    ]
    deny = next(m for m in messages if m.get("id") == "c-1")
    assert deny["result"] == {"answer": "deny"}
    assert methods.index("reply:c-1") < methods.index("session.cancel")
    cancel = next(m for m in messages if m.get("method") == "session.cancel")
    start = next(m for m in messages if m.get("method") == "session.start")
    assert cancel["params"]["session"] == start["params"]["session"]
    assert methods.index("session.cancel") < methods.index("session.close")
    assert "eof" in methods  # stdin was closed and the child reaped


@pytest.mark.parametrize(
    ("stop", "code", "text", "kind"),
    [
        ("error", 3, 'engine: provider: http 401: {"error":"expired"}', "provider_auth"),
        ("error", 3, "provider: http 403: forbidden", "provider_auth"),
        ("error", 3, "provider: http 402: no credit", "provider_credits"),
        ("error", 3, "provider: http 429: slow down", "provider_rate_limit"),
        ("error", 3, "provider: http 503: upstream", "provider_outage"),
        ("error", 3, "provider: dial tcp: i/o timeout", "provider_other"),
        ("error", 4, "engine: journal write refused", "harness_error"),
        ("max_turns", 4, "turn cap", "max_turns"),
        ("verification_failed", 1, "`go test` exited 1", "verification_failed"),
        ("budget_exhausted", 1, "", "budget_exhausted"),
        ("cancelled", 1, "context canceled", "cancelled"),
        ("mystery", 9, "", "protocol_error"),
    ],
)
async def test_each_failure_is_a_distinct_kind_carrying_the_real_text(
    fake: Callable[[list[Any]], str],
    tmp_path: Path,
    stop: str,
    code: int,
    text: str,
    kind: str,
) -> None:
    assert failure_kind_for(stop, code, text) == kind
    binary = fake(
        [
            {"start": True},
            {"emit": respond(stop, code)},
            {"close": [ended(stop, code, text)]},
            {"eof": []},
        ]
    )
    outcome = await run(binary, tmp_path)

    assert outcome.kind == "failed"
    assert outcome.failure_kind == kind
    assert outcome.reason is not None and text in outcome.reason


async def test_an_edit_that_landed_does_not_hide_a_failed_stop(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        [
            {"start": True},
            {"emit": event({"kind": "edit_applied", "path": "a.py"})},
            {"emit": respond("verification_failed", 1)},
            {"close": [ended("verification_failed", 1, "`pytest` exited 1")]},
            {"eof": []},
        ]
    )
    outcome = await run(binary, tmp_path)
    assert (outcome.kind, outcome.failure_kind) == ("failed", "verification_failed")
    assert outcome.edited_paths == ["a.py"]


async def test_the_failure_text_is_redacted_of_the_credential_the_child_carried(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    text = f"provider: http 401: bad key {KEY}"
    binary = fake(
        [
            {"start": True},
            {"emit": respond("error", 3)},
            {"close": [ended("error", 3, text)]},
            {"eof": []},
        ]
    )
    outcome = await run(binary, tmp_path, env={"OPENROUTER_API_KEY": KEY})
    assert outcome.reason is not None
    assert KEY not in outcome.reason
    assert "http 401" in outcome.reason


async def test_a_usage_error_reply_is_a_failed_outcome_not_an_exception(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        [
            {"start": True},
            {
                "emit": {
                    "jsonrpc": "2.0",
                    "id": "$start_id",
                    "error": {"code": -32003, "message": "consent_mode is required"},
                }
            },
            {"eof": []},
        ]
    )
    outcome = await run(binary, tmp_path)
    assert (outcome.kind, outcome.failure_kind) == ("failed", "protocol_error")
    assert outcome.reason is not None and "-32003" in outcome.reason


async def test_a_missing_credential_at_open_is_open_failed(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        [
            {"start": True},
            {
                "emit": {
                    "jsonrpc": "2.0",
                    "id": "$start_id",
                    "error": {"code": -32002, "message": "OPENROUTER_API_KEY is not set"},
                }
            },
            {"eof": []},
        ]
    )
    outcome = await run(binary, tmp_path)
    assert outcome.failure_kind == "open_failed"


async def test_a_child_that_dies_before_answering_is_a_delegation_error_with_its_stderr(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = tmp_path / "kopicode"
    binary.write_text("#!/bin/sh\necho 'panic: boom' >&2\nexit 2\n")
    binary.chmod(0o755)
    with pytest.raises(DelegationError, match="panic: boom"):
        await run(str(binary), tmp_path)


async def test_a_missing_binary_is_a_delegation_error(tmp_path: Path) -> None:
    with pytest.raises(DelegationError, match="not found"):
        await run("kopicode-binary-that-does-not-exist", tmp_path)


async def test_a_timeout_cancels_the_session_and_is_a_round_timeout_outcome(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        [
            {"start": True},
            {"wait_for": "session.cancel"},
            {"emit": respond("cancelled", 1)},
            {"close": [ended("cancelled", 1)]},
            {"eof": []},
        ]
    )
    outcome = await run_kopicode_serve(
        binary=binary, task_text="x", root=str(tmp_path), policy=POLICY, timeout=0.3
    )
    assert outcome.kind == "failed" and outcome.failure_kind == "round_timeout"
    assert "longer than 0.3 seconds" in (outcome.reason or "")
    assert any(m.get("method") == "session.cancel" for m in sent(tmp_path))


# -- an agent stuck on its environment (ADR-0029, V5-E5) ---------------------------------


def shell_result(output: str, code: int = 1) -> dict[str, Any]:
    return {
        "type": "ToolResult",
        "payload": {
            "call_id": "c",
            "tool": "run_shell",
            "exit_code": code,
            "output": {"inline": output, "size": len(output)},
        },
    }


def shell_done(code: int = 1) -> dict[str, Any]:
    return event({"kind": "tool_result", "tool": "run_shell", "exit_code": code, "size": 9})


def stuck_scenario(outputs: list[tuple[str, int]], *, then: list[Any]) -> list[Any]:
    steps: list[Any] = [{"start": True}]
    for text, code in outputs:
        steps += [{"record": [shell_result(text, code)]}, {"emit": shell_done(code)}]
    return steps + then


MISSING = "run_shell `pytest`: exited 1\nModuleNotFoundError: No module named 'numpy'"


async def test_n_environment_failures_in_a_row_cancel_the_session_with_the_evidence(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        stuck_scenario(
            [(MISSING, 1)] * 3,
            then=[
                {"wait_for": "session.cancel"},
                {"emit": respond("cancelled", 1)},
                {"close": [ended("cancelled", 1)]},
                {"eof": []},
            ],
        )
    )
    outcome = await run(binary, tmp_path, stuck_threshold=3)
    assert outcome.kind == "failed"
    assert outcome.failure_kind == "environment_stuck"
    assert "3 shell commands in a row" in outcome.reason
    assert "No module named 'numpy'" in outcome.detail
    assert outcome.record == "/r"
    assert any(m.get("method") == "session.cancel" for m in sent(tmp_path))


async def test_the_evidence_is_redacted_before_it_leaves_the_transport(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        stuck_scenario(
            [(f"command not found: tool --key {KEY}", 127)],
            then=[
                {"wait_for": "session.cancel"},
                {"emit": respond("cancelled", 1)},
                {"close": [ended("cancelled", 1)]},
                {"eof": []},
            ],
        )
    )
    outcome = await run(binary, tmp_path, stuck_threshold=1, env={"OPENROUTER_API_KEY": KEY})
    assert outcome.failure_kind == "environment_stuck"
    assert KEY not in outcome.detail


async def test_a_success_in_between_keeps_the_session_running(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        stuck_scenario(
            [(MISSING, 1), (MISSING, 1), ("3 passed", 0), (MISSING, 1), (MISSING, 1)],
            then=[{"emit": respond()}, {"close": [ended()]}, {"eof": []}],
        )
    )
    outcome = await run(binary, tmp_path, stuck_threshold=3)
    assert outcome.kind != "failed"
    assert not any(m.get("method") == "session.cancel" for m in sent(tmp_path))


async def test_a_threshold_of_zero_never_cancels(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        stuck_scenario(
            [(MISSING, 1)] * 6, then=[{"emit": respond()}, {"close": [ended()]}, {"eof": []}]
        )
    )
    await run(binary, tmp_path, stuck_threshold=0)
    assert not any(m.get("method") == "session.cancel" for m in sent(tmp_path))


async def test_with_no_readable_record_nothing_is_cancelled(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    steps: list[Any] = [
        {"start": True},
        *([{"emit": shell_done(1)}] * 6),
        {"emit": respond()},
        {"close": [ended()]},
        {"eof": []},
    ]
    binary = fake(steps)
    outcome = await run(binary, tmp_path, stuck_threshold=2)
    assert outcome.kind != "failed"
    assert not any(m.get("method") == "session.cancel" for m in sent(tmp_path))


# -- the resident pool ------------------------------------------------------------------


def pids(tmp_path: Path) -> list[int]:
    return [m["pid"] for m in sent(tmp_path) if "pid" in m]


def one_delegation(*extra: Any) -> list[Any]:
    return [
        {"start": True},
        {"emit": respond()},
        {"close": [ended()]},
        *extra,
    ]


async def test_delegations_on_one_pool_share_a_child_and_each_session_is_closed(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(one_delegation() + one_delegation() + [{"eof": []}])
    pool = ServePool()
    try:
        first = await run(binary, tmp_path, pool=pool)
        second = await run(binary, tmp_path, pool=pool)
    finally:
        await pool.aclose()

    assert (first.kind, second.kind) == ("completed", "completed")
    assert len(pids(tmp_path)) == 1  # one process served both
    messages = sent(tmp_path)
    starts = [m for m in messages if m.get("method") == "session.start"]
    closes = [m for m in messages if m.get("method") == "session.close"]
    assert len(starts) == len(closes) == 2
    assert starts[0]["params"]["session"] != starts[1]["params"]["session"]
    assert [c["params"]["session"] for c in closes] == [s["params"]["session"] for s in starts]


async def test_different_credentials_get_different_children(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake([*one_delegation(), {"eof": []}])
    pool = ServePool()
    try:
        await run(binary, tmp_path, pool=pool, env={"OPENROUTER_API_KEY": KEY})
        await run(binary, tmp_path, pool=pool, env={"OPENROUTER_API_KEY": KEY + "x"})
    finally:
        await pool.aclose()
    assert len(set(pids(tmp_path))) == 2


async def test_a_child_that_died_between_delegations_is_replaced(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(one_delegation({"exit": True}))
    pool = ServePool()
    try:
        await run(binary, tmp_path, pool=pool)
        await asyncio.sleep(0.3)  # let the reader notice the exit
        outcome = await run(binary, tmp_path, pool=pool)
    finally:
        await pool.aclose()
    assert outcome.kind == "completed"
    assert len(set(pids(tmp_path))) == 2


async def test_an_unconfirmed_close_kills_the_child_so_no_lock_is_left_held(
    fake: Callable[[list[Any]], str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(kopicode_serve, "_CLOSE_GRACE", 0.3)
    # After the turn's response this fake reads until stdin closes and never acks the close.
    binary = fake([{"start": True}, {"emit": respond()}, {"wait_for": "never"}])
    pool = ServePool()
    try:
        outcome = await run(binary, tmp_path, pool=pool)  # the outcome is already known
        assert outcome.kind == "completed"
        await run(binary, tmp_path, pool=pool)
    finally:
        await pool.aclose()
    assert len(set(pids(tmp_path))) == 2  # the first child was not reused


async def test_a_consent_request_for_a_session_nobody_registered_is_denied(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        [
            {"start": True},
            {
                "consent": {
                    "id": "c-1",
                    "session": "someone-else",
                    "kind": "run_shell",
                    "detail": SH + "uv run pytest",
                }
            },
            {"emit": respond()},
            {"close": [ended()]},
            {"eof": []},
        ]
    )
    records: list[ConsentRecord] = []
    await run(binary, tmp_path, on_consent=records.append)
    reply = next(m for m in sent(tmp_path) if m.get("id") == "c-1")
    assert reply["result"] == {"answer": "deny"}
    assert records[0].rule == "unknown_session"


async def test_the_failure_text_arrives_with_the_close_not_at_shutdown(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    text = "provider: http 401: expired"
    binary = fake(
        [
            {"start": True},
            {"emit": respond("error", 3)},
            {"close": [ended("error", 3, text)]},
            {"eof": []},
        ]
    )
    pool = ServePool()
    try:
        outcome = await run(binary, tmp_path, pool=pool)
    finally:
        await pool.aclose()
    assert outcome.failure_kind == "provider_auth"


# -- a process started elsewhere (a sandbox's streaming spawn, KAN-1793) ------------------


async def test_a_process_from_a_factory_is_driven_exactly_like_a_host_spawn(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        [
            {"start": True},
            {
                "consent": {
                    "id": "c-1",
                    "kind": "run_shell",
                    "detail": SH + "uv run pytest -q",
                }
            },
            {"emit": respond()},
            {"close": [ended()]},
            {"eof": []},
        ]
    )
    spawned: list[int] = []

    async def factory() -> asyncio.subprocess.Process:
        process = await asyncio.create_subprocess_exec(
            binary,
            "serve",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        spawned.append(process.pid)
        return process

    outcome = await run(binary, tmp_path, process_factory=factory)

    assert len(spawned) == 1
    assert outcome.kind == "completed"
    assert [(d.answer, d.detail) for d in outcome.consent_decisions] == [
        ("allow", SH + "uv run pytest -q")
    ]


async def test_a_factory_that_fails_to_spawn_is_a_delegation_error(tmp_path: Path) -> None:
    async def factory() -> asyncio.subprocess.Process:
        raise SandboxError("container gone")

    with pytest.raises(DelegationError, match="container gone"):
        await run("unused", tmp_path, process_factory=factory)


async def test_a_factory_child_cannot_be_pooled(tmp_path: Path) -> None:
    async def factory() -> asyncio.subprocess.Process:
        raise AssertionError("must not be reached")

    with pytest.raises(ValueError, match="pool"):
        await run("unused", tmp_path, process_factory=factory, pool=ServePool())


class _FakeStreamingProvider:
    """Records what the backend asks of a sandbox; ``spawn`` starts the fake kopicode on the
    host, standing in for ``docker exec -i``."""

    BACKEND_NAME = "fake"

    def __init__(self, binary: str) -> None:
        self._binary = binary
        self.calls: list[str] = []
        self.spec: Any = None
        self.spawned: tuple[list[str], str | None] | None = None

    async def create(self, spec: Any = None) -> Any:
        from cuttlefish.sandbox.provider import SandboxHandle

        self.calls.append("create")
        self.spec = spec
        return SandboxHandle("sbx-1")

    async def spawn(
        self, handle: Any, command: Any, *, cwd: str | None = None
    ) -> asyncio.subprocess.Process:
        self.calls.append("spawn")
        self.spawned = (list(command), cwd)
        return await asyncio.create_subprocess_exec(
            self._binary,
            "serve",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def destroy(self, handle: Any) -> None:
        self.calls.append("destroy")


async def test_a_streaming_sandbox_runs_serve_inside_it_and_destroys_it_after(
    fake: Callable[[list[Any]], str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cuttlefish.agents.kopicode import KopicodeBackend

    binary = fake(
        [
            {"start": True},
            {
                "consent": {
                    "id": "c-1",
                    "kind": "run_shell",
                    "detail": SH + "uv run pytest -q",
                }
            },
            {
                "consent": {
                    "id": "c-2",
                    "kind": "run_shell",
                    "detail": SH + "rm -rf x && true",
                }
            },
            {"emit": respond()},
            {"close": [ended()]},
            {"eof": []},
        ]
    )
    provider = _FakeStreamingProvider(binary)
    outcome = await KopicodeBackend(binary).delegate(
        task_text="do it",
        root=str(tmp_path),
        allow=[["uv", "run", "pytest"]],
        secrets={},
        sandbox_provider=provider,  # type: ignore[arg-type]
    )

    assert provider.calls == ["create", "spawn", "destroy"]  # destroy last: the real cleanup
    assert provider.spawned == (["/usr/local/bin/kopicode", "serve"], str(tmp_path))
    mounts = provider.spec.mounts
    assert mounts[str(tmp_path)] == str(tmp_path)
    assert "/usr/local/bin/kopicode" in mounts.values()
    assert not any("policy" in target for target in mounts.values())  # consent is live
    assert [(d.answer, d.rule) for d in outcome.consent_decisions] == [
        ("allow", outcome.consent_decisions[0].rule),
        ("deny", "not_a_plain_word_list"),
    ]


async def test_the_sandbox_is_destroyed_even_when_the_delegation_fails(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    from cuttlefish.agents.kopicode import KopicodeBackend

    binary = fake([{"start": True}, {"exit": True}])
    provider = _FakeStreamingProvider(binary)
    with pytest.raises(DelegationError):
        await KopicodeBackend(binary).delegate(
            task_text="do it",
            root=str(tmp_path),
            allow=None,
            secrets={},
            sandbox_provider=provider,  # type: ignore[arg-type]
        )
    assert provider.calls[-1] == "destroy"


async def test_the_sessions_own_record_directory_is_carried_on_the_outcome(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    """A failure should point at kopicode's own record of it (ADR-0029)."""
    binary = fake(
        [
            {"start": True},
            {"emit": respond(stop="max_turns", exit_code=4)},
            {"close": [ended(reason="max_turns", exit_code=4)]},
            {"eof": []},
        ]
    )

    outcome = await run(binary, tmp_path)

    assert outcome.kind == "failed"
    assert outcome.failure_kind == "max_turns"
    assert outcome.record == "/r"


async def test_the_help_probe_does_not_inherit_cuttlefishs_own_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found by the V5-E1b CLI run: the `serve --help` probe skipped `merge_env` (ADR-0029)."""
    from cuttlefish.delegate import kopicode_serve

    own = tmp_path / ".venv"
    (own / "bin").mkdir(parents=True)
    seen = tmp_path / "seen.txt"
    script = tmp_path / "kopicode-probe"
    script.write_text(f'#!/bin/sh\necho "$VIRTUAL_ENV|$PATH" > {seen}\necho "usage: serve"\n')
    script.chmod(0o755)
    monkeypatch.setattr("sys.prefix", str(own))
    monkeypatch.setattr("sys.base_prefix", "/usr")
    monkeypatch.setenv("VIRTUAL_ENV", str(own))
    monkeypatch.setenv("PATH", f"{own / 'bin'}:/usr/bin:/bin")
    monkeypatch.setattr(kopicode_serve, "_TIMEOUT_FLAG_SUPPORT", {})

    await kopicode_serve.serve_supports_consent_timeout(str(script))

    virtual_env, path = seen.read_text().strip().split("|", 1)
    assert virtual_env == ""
    assert str(own) not in path


# -- a whole-file write has no path in the stream, only in the record (ADR-0030) ----------


def write_record(call_id: str, path: str, *, fails: bool = False) -> list[dict[str, Any]]:
    parsed = {
        "type": "ToolCallParsed",
        "payload": {
            "call_id": call_id,
            "tool": "write_file",
            "args": {"path": path, "content": "x"},
        },
    }
    result: dict[str, Any] = {
        "type": "ToolResult",
        "payload": {"call_id": call_id, "tool": "write_file", "output": {"inline": "ok"}},
    }
    if fails:
        result["payload"]["error_kind"] = "denied"
    return [parsed, result]


def cut_write_events() -> list[Any]:
    """What the stream shows for a write with real content: the call's arguments cut at 120
    characters (no usable path) and a tool_result."""
    cut = '{"content":"' + "x" * 100 + "…"
    return [
        {"emit": event({"kind": "tool_call_parsed", "tool": "write_file", "detail": cut})},
        {"emit": event({"kind": "tool_result", "tool": "write_file", "size": 9})},
    ]


async def test_a_file_written_with_cut_arguments_is_still_an_edit_from_the_record(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        [
            {"start": True},
            *cut_write_events(),
            {
                "record": [
                    *write_record("a", "src/a.py"),
                    *write_record("b", "tests/b.py", fails=True),
                ]
            },
            {"emit": respond("max_turns", 4)},
            {"close": [ended("max_turns", 4)]},
            {"eof": []},
        ]
    )
    outcome = await run(binary, tmp_path)
    assert outcome.kind == "failed" and outcome.failure_kind == "max_turns"
    assert outcome.edited_paths == ["src/a.py"]  # the failed write is not an edit


async def test_a_refusal_alongside_a_real_write_is_not_a_refused_round(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    denied = event({"kind": "permission_decided", "decision": "deny", "reason": "denied"})
    binary = fake(
        [
            {"start": True},
            *cut_write_events(),
            {"emit": denied},
            {"record": write_record("a", "src/a.py")},
            {"emit": respond()},
            {"close": [ended()]},
            {"eof": []},
        ]
    )
    outcome = await run(binary, tmp_path)
    assert outcome.kind == "completed" and outcome.edited_paths == ["src/a.py"]


# -- a round that fills the model's context window (ADR-0030) ---------------------------


def pressure_scenario(context_tokens: int, window: int | None, *, then: list[Any]) -> list[Any]:
    usage: dict[str, Any] = {"context_tokens": context_tokens, "requests": 1, "turns": 1}
    if window is not None:
        usage["context_window"] = window
    return [
        {"start": True},
        {"emit": event({"kind": "edit_applied", "path": "a.py"})},
        {"emit": event({"kind": "provider_response", "size": 9})},
        {"usage": usage},
        *then,
    ]


_CANCELLED = [
    {"wait_for": "session.cancel"},
    {"emit": respond("cancelled", 1)},
    {"close": [ended("cancelled", 1)]},
    {"eof": []},
]


async def test_a_context_past_the_limit_cancels_the_round_as_context_pressure(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(pressure_scenario(210_000, 262_144, then=_CANCELLED))
    outcome = await run(binary, tmp_path, context_limit=0.75)
    assert outcome.kind == "failed" and outcome.failure_kind == "context_pressure"
    assert "210000 of 262144 context tokens" in (outcome.reason or "")
    assert outcome.edited_paths == ["a.py"]  # what the round did before is kept
    methods = [m.get("method") for m in sent(tmp_path)]
    assert "session.usage" in methods and "session.cancel" in methods


async def test_a_context_under_the_limit_is_left_alone(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        pressure_scenario(
            50_000, 262_144, then=[{"emit": respond()}, {"close": [ended()]}, {"eof": []}]
        )
    )
    outcome = await run(binary, tmp_path, context_limit=0.75)
    assert outcome.kind == "completed"
    assert not any(m.get("method") == "session.cancel" for m in sent(tmp_path))


async def test_an_unknown_window_never_ends_a_round(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        pressure_scenario(
            900_000, None, then=[{"emit": respond()}, {"close": [ended()]}, {"eof": []}]
        )
    )
    outcome = await run(binary, tmp_path, context_limit=0.75)
    assert outcome.kind == "completed"


async def test_no_limit_means_no_usage_question(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        [
            {"start": True},
            {"emit": event({"kind": "provider_response", "size": 9})},
            {"emit": respond()},
            {"close": [ended()]},
            {"eof": []},
        ]
    )
    await run(binary, tmp_path)
    assert not any(m.get("method") == "session.usage" for m in sent(tmp_path))


# -- the cost kopicode reports (v0.4.0 usage.cost) --------------------------------------


@pytest.mark.parametrize(
    ("usage", "cost"),
    [({"cost_usd": 0.0123, "total": 900}, 0.0123), ({"total": 900}, None), (None, None)],
)
async def test_the_cost_kopicode_reports_is_the_rounds_cost_and_never_estimated(
    fake: Callable[[list[Any]], str], tmp_path: Path, usage: dict[str, Any] | None, cost: Any
) -> None:
    done = respond()
    if usage is not None:
        done["result"]["usage"] = usage
    binary = fake([{"start": True}, {"emit": done}, {"close": [ended()]}, {"eof": []}])
    outcome = await run(binary, tmp_path)
    assert outcome.cost_usd == cost


# -- a resident child logs each session in its own team's context -----------------------


async def test_a_consent_is_logged_in_the_context_of_the_session_that_asked(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    from cuttlefish import logsetup

    consent = {"id": "c-1", "kind": "run_shell", "detail": SH + "uv run pytest -q"}
    binary = fake(
        [
            {"start": True},
            {"emit": respond()},
            {"close": [ended()]},
            {"start": True},
            {"consent": consent},
            {"emit": respond()},
            {"close": [ended()]},
            {"eof": []},
        ]
    )
    seen: list[dict[str, str]] = []

    class Spy(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(dict(logsetup._CONTEXT.get() or {}))

    logger = logging.getLogger("cuttlefish.delegate.consent")
    handler = Spy()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    pool = ServePool()
    try:
        # One resident child, spawned under the first team and reused by the second.
        with logsetup.bind(team="team-1"):
            await run(binary, tmp_path, pool=pool)
        with logsetup.bind(team="team-2", role="builder"):
            await run(binary, tmp_path, pool=pool)
    finally:
        await pool.aclose()
        logger.removeHandler(handler)
    assert seen and all(c.get("team") == "team-2" for c in seen), seen
