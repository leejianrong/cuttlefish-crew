"""Unit: the kopicode serve transport, against a scripted fake child (stdin/stdout).

The fake (``fake_kopicode_serve.py``) speaks kopicode's serve wire from a JSON scenario and
logs every line the client sends, so each test asserts on what the client *sent* as well as
the outcome it returned.
"""

from __future__ import annotations

import asyncio
import json
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from cuttlefish.agents.outcome import DelegationError
from cuttlefish.delegate.consent import ConsentDecision, ConsentPolicy
from cuttlefish.delegate.kopicode_serve import (
    ConsentRecord,
    failure_kind_for,
    run_kopicode_serve,
)

FAKE = Path(__file__).with_name("fake_kopicode_serve.py")
POLICY = ConsentPolicy([["uv", "run", "pytest"]])
KEY = "sk-or-v1-" + "0" * 40


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
            {"consent": {"id": "c-1", "kind": "run_shell", "detail": "uv run pytest -q"}},
            {"emit": event({"kind": "edit_applied", "path": "a.py"})},
            {"emit": event({"kind": "provider_response", "size": 2})},
            {"emit": respond()},
            {"eof": [ended()]},
        ]
    )
    outcome = await run(binary, tmp_path)

    assert outcome.kind == "completed"
    assert outcome.edited_paths == ["a.py"]
    assert outcome.tokens == 42
    assert outcome.failure_kind is None
    messages = sent(tmp_path)
    assert messages[0]["params"]["consent_mode"] == "remote_interactive"
    assert set(messages[0]["params"]) == {"session", "dir", "prompt", "consent_mode"}
    reply = next(m for m in messages if m.get("id") == "c-1")
    assert reply == {"jsonrpc": "2.0", "id": "c-1", "result": {"answer": "allow"}}


async def test_a_denied_command_is_answered_deny_and_the_outcome_is_refused(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        [
            {"start": True},
            {"consent": {"id": "c-1", "kind": "run_shell", "detail": "uv run pytest && rm -rf /"}},
            {"emit": event({"kind": "permission_decided", "decision": "deny", "reason": "remote"})},
            {"emit": respond()},
            {"eof": [ended()]},
        ]
    )
    records: list[ConsentRecord] = []
    outcome = await run(binary, tmp_path, on_consent=records.append)

    assert outcome.kind == "refused"
    reply = next(m for m in sent(tmp_path) if m.get("id") == "c-1")
    assert reply["result"] == {"answer": "deny"}
    assert [(r.answer, r.rule) for r in records] == [("deny", "not_a_plain_word_list")]
    assert records[0].detail == "uv run pytest && rm -rf /"


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
            {"eof": [ended()]},
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
            {"eof": [ended()]},
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
            {"consent": {"id": "c-1", "kind": "run_shell", "detail": "uv run pytest"}},
            {"emit": respond()},
            {"eof": [ended()]},
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
            {"consent": {"id": "c-1", "kind": "run_shell", "detail": "x"}},
            {"emit": respond()},
            {"eof": [ended()]},
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
            {"consent": {"id": "c-1", "kind": "run_shell", "detail": "uv run pytest"}, "wait": 0.1},
            {
                "emit": event(
                    {"kind": "permission_decided", "decision": "deny", "reason": "timeout"}
                )
            },
            {"emit": respond()},
            {"eof": [ended()]},
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
            {"consent": {"id": "c-1", "kind": "run_shell", "detail": "uv run pytest"}, "wait": 10},
            {"wait_for": "session.cancel"},
            {"emit": respond("cancelled", 1)},
            {"eof": [ended("cancelled", 1, "context canceled")]},
        ]
    )
    task = asyncio.create_task(run(binary, tmp_path, policy=never_answers))
    await asyncio.wait_for(asking.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    messages = sent(tmp_path)
    methods = [m.get("method") or ("reply:" + m["id"] if "id" in m else "eof") for m in messages]
    deny = next(m for m in messages if m.get("id") == "c-1")
    assert deny["result"] == {"answer": "deny"}
    assert methods.index("reply:c-1") < methods.index("session.cancel")
    cancel = next(m for m in messages if m.get("method") == "session.cancel")
    assert cancel["params"]["session"] == messages[0]["params"]["session"]
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
        [{"start": True}, {"emit": respond(stop, code)}, {"eof": [ended(stop, code, text)]}]
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
            {"eof": [ended("verification_failed", 1, "`pytest` exited 1")]},
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
        [{"start": True}, {"emit": respond("error", 3)}, {"eof": [ended("error", 3, text)]}]
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


async def test_a_timeout_cancels_the_session_and_raises(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake(
        [
            {"start": True},
            {"wait_for": "session.cancel"},
            {"emit": respond("cancelled", 1)},
            {"eof": [ended("cancelled", 1)]},
        ]
    )
    with pytest.raises(DelegationError, match="timed out"):
        await run_kopicode_serve(
            binary=binary, task_text="x", root=str(tmp_path), policy=POLICY, timeout=0.3
        )
    assert any(m.get("method") == "session.cancel" for m in sent(tmp_path))
