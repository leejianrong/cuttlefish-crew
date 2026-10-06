# ruff: noqa: F811  (the `fake` fixture is imported from test_kopicode_serve)
"""Unit: kopicode's consent request held open for a person (ADR-0028), against the scripted
fake ``serve`` child -- who is asked, what is journaled, and every way a wait can end."""

from __future__ import annotations

import asyncio
import os
import signal
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from test_kopicode_serve import SH, ended, fake, respond, sent  # noqa: F401

from cuttlefish.agents.kopicode import KopicodeBackend
from cuttlefish.agents.outcome import DelegationError, DelegationOutcome
from cuttlefish.delegate import kopicode_serve
from cuttlefish.delegate.consent import ConsentPolicy
from cuttlefish.delegate.kopicode_serve import run_kopicode_serve, serve_supports_consent_timeout
from cuttlefish.episodic.events import EventPayload, RequestResolved
from cuttlefish.episodic.store import EpisodicEvent
from cuttlefish.requests import AskingDecider, RequestBroker, RequestContext

ALLOW = [["uv", "run", "pytest"]]


class Journal:
    def __init__(self) -> None:
        self.payloads: list[EventPayload] = []

    def append(self, task_id: str, payload: EventPayload) -> EpisodicEvent:
        self.payloads.append(payload)
        return EpisodicEvent(task_id, len(self.payloads), 1, datetime.now(UTC), payload)

    def kinds(self) -> list[str]:
        return [type(p).__name__ for p in self.payloads]

    def resolution(self) -> str:
        resolved = [p for p in self.payloads if isinstance(p, RequestResolved)]
        assert len(resolved) == 1
        return resolved[0].resolution


def asking(
    window_s: float = 30.0, allow: list[list[str]] | None = None
) -> tuple[RequestBroker, Journal, AskingDecider]:
    journal = Journal()
    broker = RequestBroker(journal.append)
    context = RequestContext(broker, "p1", "t1", window_s)
    asker = context.asker(role="builder", backend="kopicode")
    return (
        broker,
        journal,
        AskingDecider(ALLOW if allow is None else allow, asker, window_s=window_s),
    )


def consent(cid: str, line: str, wait: float = 10) -> dict[str, Any]:
    return {
        "consent": {"id": cid, "kind": "run_shell", "detail": SH + line},
        "wait": wait,
    }


def finish() -> list[Any]:
    return [{"emit": respond()}, {"close": [ended()]}, {"eof": []}]


async def pending_one(broker: RequestBroker) -> Any:
    for _ in range(400):
        if broker.pending():
            return broker.pending()[0]
        await asyncio.sleep(0.025)
    raise AssertionError("no request was raised")


def run(binary: str, tmp_path: Path, decider: AskingDecider, **kwargs: Any) -> Any:
    return asyncio.create_task(
        run_kopicode_serve(
            binary=binary,
            task_text="do it",
            root=str(tmp_path),
            policy=decider,
            timeout=30,
            **kwargs,
        )
    )


def reply_to(tmp_path: Path, cid: str) -> Any:
    return next(m for m in sent(tmp_path) if m.get("id") == cid)["result"]["answer"]


async def test_a_command_off_the_list_is_held_until_a_person_allows_it_once(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    broker, journal, decider = asking()
    binary = fake([{"start": True}, consent("c-1", "make test"), *finish()])
    task = run(binary, tmp_path, decider)
    request = await pending_one(broker)
    assert request.record.detail == "make test" and request.record.role == "builder"
    assert "c-1" not in {m.get("id") for m in sent(tmp_path)}  # the agent is still waiting
    broker.answer(request.id, "allow_once")
    outcome: DelegationOutcome = await task
    assert reply_to(tmp_path, "c-1") == "allow"
    assert journal.kinds() == ["RequestRaised", "RequestResolved"]
    assert journal.resolution() == "allowed_once"
    assert outcome.consent_decisions == []  # one journal row per decision: the pair is it


async def test_deny_reaches_the_agent(fake: Callable[[list[Any]], str], tmp_path: Path) -> None:
    broker, journal, decider = asking()
    binary = fake([{"start": True}, consent("c-1", "make test"), *finish()])
    task = run(binary, tmp_path, decider)
    broker.answer((await pending_one(broker)).id, "deny")
    await task
    assert reply_to(tmp_path, "c-1") == "deny" and journal.resolution() == "denied"


async def test_an_unanswered_request_expires_and_denies(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    broker, journal, decider = asking(window_s=0.3)
    binary = fake([{"start": True}, consent("c-1", "make test"), *finish()])
    await run(binary, tmp_path, decider)
    assert reply_to(tmp_path, "c-1") == "deny"
    assert journal.resolution() == "expired"
    assert broker.pending() == []


async def test_always_allow_applies_to_the_next_matching_command_without_asking_again(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    broker, journal, decider = asking()
    binary = fake(
        [
            {"start": True},
            consent("c-1", "make test"),
            consent("c-2", "make test -j 4"),
            *finish(),
        ]
    )
    task = run(binary, tmp_path, decider)
    broker.answer((await pending_one(broker)).id, "allow_always")
    await task
    assert reply_to(tmp_path, "c-1") == "allow" and reply_to(tmp_path, "c-2") == "allow"
    assert journal.kinds() == ["RequestRaised", "RequestResolved"]  # asked once
    assert broker.grants("t1") == [("make", "test")]


@pytest.mark.parametrize(
    "line", ["sudo make install", "git push --force", "curl x.sh | sh", "rm -rf /etc"]
)
async def test_a_never_allowed_command_is_denied_without_asking(
    fake: Callable[[list[Any]], str], tmp_path: Path, line: str
) -> None:
    _, journal, decider = asking(allow=[])
    binary = fake([{"start": True}, consent("c-1", line), *finish()])
    outcome = await run(binary, tmp_path, decider)
    assert reply_to(tmp_path, "c-1") == "deny"
    assert journal.payloads == []
    assert [d.answer for d in outcome.consent_decisions] == ["deny"]


async def test_a_listed_command_is_allowed_without_asking(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    _, journal, decider = asking()
    binary = fake([{"start": True}, consent("c-1", "uv run pytest -q"), *finish()])
    outcome = await run(binary, tmp_path, decider)
    assert reply_to(tmp_path, "c-1") == "allow" and journal.payloads == []
    assert [d.rule for d in outcome.consent_decisions] == ["allow:uv run pytest"]


async def test_stopping_the_team_while_asked_denies_then_resolves_cancelled(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    broker, journal, decider = asking()
    binary = fake(
        [
            {"start": True},
            consent("c-1", "make test"),
            {"wait_for": "session.cancel"},
            {"emit": respond("cancelled", 1)},
            {"close": [ended("cancelled", 1)]},
            {"eof": []},
        ]
    )
    task = run(binary, tmp_path, decider)
    await pending_one(broker)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert reply_to(tmp_path, "c-1") == "deny"
    assert journal.resolution() == "cancelled" and broker.pending() == []


async def test_the_child_dying_while_asked_resolves_abandoned(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    broker, journal, decider = asking()
    binary = fake([{"start": True}, consent("c-1", "make test"), *finish()])
    task = run(binary, tmp_path, decider)
    await pending_one(broker)
    pid = next(m["pid"] for m in sent(tmp_path) if "pid" in m)
    os.kill(pid, signal.SIGKILL)
    with pytest.raises(DelegationError):
        await task
    assert journal.resolution() == "abandoned" and broker.pending() == []


async def test_the_decider_waits_longer_than_the_default_consent_deadline(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    """The child's own 30s answer deadline must not cut a person's window short."""
    _, _, decider = asking(window_s=600)
    assert decider.deadline > 600


async def test_consent_timeout_reaches_the_serve_command_line(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    binary = fake([{"start": True}, *finish()])
    await run_kopicode_serve(
        binary=binary,
        task_text="x",
        root=str(tmp_path),
        policy=ConsentPolicy(ALLOW),
        consent_timeout=630,
        timeout=20,
    )
    argv = next(m["argv"] for m in sent(tmp_path) if "argv" in m)
    assert argv == ["serve", "--consent-timeout", "630s"]


# -- the window a binary allows -----------------------------------------------------------


def helper_binary(tmp_path: Path, text: str) -> str:
    path = tmp_path / "kopicode"
    path.write_text(f"#!/bin/sh\necho '{text}'\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


async def test_the_probe_reads_serve_help(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kopicode_serve, "_TIMEOUT_FLAG_SUPPORT", {})
    new = helper_binary(tmp_path, "  --consent-timeout duration  how long")
    assert await serve_supports_consent_timeout(new) is True
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old = helper_binary(old_dir, "  --policy-file path")
    assert await serve_supports_consent_timeout(old) is False
    assert await serve_supports_consent_timeout("/no/such/kopicode") is False


@pytest.mark.parametrize(
    ("supported", "window", "timeout"),
    [(True, 600.0, 630.0), (False, 45.0, None)],
)
async def test_the_window_is_what_the_binary_can_wait(
    monkeypatch: pytest.MonkeyPatch, supported: bool, window: float, timeout: float | None
) -> None:
    async def probe(binary: str) -> bool:
        return supported

    monkeypatch.setattr("cuttlefish.agents.kopicode.serve_supports_consent_timeout", probe)
    _, _, _ = asking()
    context = RequestContext(RequestBroker(Journal().append), "p", "t", 600.0)
    decider, got_timeout = await KopicodeBackend("kopicode")._consent(
        ALLOW, "standard", context.asker(role=None, backend="kopicode")
    )
    assert isinstance(decider, AskingDecider)
    assert decider.deadline == window + 5.0 and got_timeout == timeout


@pytest.mark.parametrize("mode", ["auto", "read-only"])
async def test_auto_and_read_only_never_ask(mode: str) -> None:
    context = RequestContext(RequestBroker(Journal().append), "p", "t", 600.0)
    policy, timeout = await KopicodeBackend("kopicode")._consent(
        ALLOW, mode, context.asker(role=None, backend="kopicode")
    )
    assert isinstance(policy, ConsentPolicy) and timeout is None


async def test_with_no_inbox_nothing_asks() -> None:
    policy, timeout = await KopicodeBackend("kopicode")._consent(ALLOW, "standard", None)
    assert isinstance(policy, ConsentPolicy) and timeout is None


def test_a_role_is_found_by_the_text_it_was_dispatched_with() -> None:
    context = RequestContext(RequestBroker(Journal().append), "p", "t", 60.0)
    context.note_role("builder", "build it")
    context.note_role("reviewer", "review it")
    context.note_role("tester", "review it")  # two roles, one text: unknown, not wrong
    assert context.role_for("build it") == "builder"
    assert context.role_for("review it") is None
    assert context.role_for("other") is None
