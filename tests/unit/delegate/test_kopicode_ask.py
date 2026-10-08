# ruff: noqa: F811  (the `fake` fixture is imported from test_kopicode_serve)
"""Unit: the model's own question put to a person live (kopicode ``ask.request``), against the
scripted fake ``serve`` child -- the wire, the journal pair, and every way the wait can end."""

from __future__ import annotations

import asyncio
import json
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from test_kopicode_asking import Journal, finish, pending_one
from test_kopicode_serve import fake, sent  # noqa: F401

from cuttlefish.agents.kopicode import KopicodeBackend
from cuttlefish.delegate import kopicode_serve
from cuttlefish.delegate.consent import ConsentPolicy
from cuttlefish.delegate.kopicode_serve import run_kopicode_serve, serve_supports_ask
from cuttlefish.episodic.events import RequestRaised, RequestResolved
from cuttlefish.requests import InvalidAnswerError, RequestBroker, RequestContext

ASK = {"id": "c-9", "question": "tabs or spaces?", "context": "gofmt is not set up"}


def setup(window_s: float = 30.0) -> tuple[RequestBroker, Journal, Any]:
    journal = Journal()
    broker = RequestBroker(journal.append)
    asker = RequestContext(broker, "p1", "t1", window_s).asker(role="builder", backend="kopicode")

    async def ask(question: str, context: str) -> str | None:
        return await asker.ask_person(question, context, window_s=window_s)

    return broker, journal, ask


def run(binary: str, tmp_path: Path, ask: Any) -> Any:
    return asyncio.create_task(
        run_kopicode_serve(
            binary=binary,
            task_text="do it",
            root=str(tmp_path),
            policy=ConsentPolicy([]),
            timeout=30,
            ask=ask,
        )
    )


def start_params(tmp_path: Path) -> dict[str, Any]:
    return next(m for m in sent(tmp_path) if m.get("method") == "session.start")["params"]


def reply(tmp_path: Path) -> dict[str, Any]:
    return next(m for m in sent(tmp_path) if m.get("id") == "c-9")


async def test_a_question_is_held_until_a_person_answers_it(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    broker, journal, ask = setup()
    task = run(fake([{"start": True}, {"ask": ASK, "wait": 10}, *finish()]), tmp_path, ask)
    request = await pending_one(broker)
    assert request.record.kind == "question" and request.record.detail == "tabs or spaces?"
    assert request.record.why == "gofmt is not set up"
    assert request.record.answers == ["answer", "decline"] and request.record.role == "builder"
    assert "c-9" not in {m.get("id") for m in sent(tmp_path)}  # the agent is still waiting
    broker.answer(request.id, "answer", text="  tabs ")
    await task
    assert start_params(tmp_path)["ask_mode"] == "remote"
    assert reply(tmp_path)["result"] == {"text": "tabs"}
    raised, resolved = journal.payloads
    assert isinstance(raised, RequestRaised) and isinstance(resolved, RequestResolved)
    assert (resolved.resolution, resolved.by, resolved.text) == ("answered", "person", "tabs")


async def test_declining_leaves_the_question_unanswered(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    broker, journal, ask = setup()
    task = run(fake([{"start": True}, {"ask": ASK, "wait": 10}, *finish()]), tmp_path, ask)
    broker.answer((await pending_one(broker)).id, "decline")
    await task
    assert "result" not in reply(tmp_path) and reply(tmp_path)["error"]["code"] == -32000
    assert journal.resolution() == "declined"


async def test_an_unanswered_question_expires(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    broker, journal, ask = setup(window_s=0.3)
    await run(fake([{"start": True}, {"ask": ASK, "wait": 10}, *finish()]), tmp_path, ask)
    assert "error" in reply(tmp_path) and journal.resolution() == "expired"
    assert broker.pending() == []


async def test_a_session_without_an_ask_handler_asks_for_no_live_ask(
    fake: Callable[[list[Any]], str], tmp_path: Path
) -> None:
    await run(fake([{"start": True}, *finish()]), tmp_path, None)
    assert "ask_mode" not in start_params(tmp_path)


async def test_an_answer_needs_text_and_the_request_stays_pending() -> None:
    broker, _, _ = setup()
    request = broker.raise_question(
        project_id="p1",
        team_id="t1",
        role=None,
        backend="kopicode",
        question="q",
        context="",
        window_s=30,
    )
    for text in (None, "", "   "):
        with pytest.raises(InvalidAnswerError):
            broker.answer(request.id, "answer", text=text)
    assert broker.pending() == [request]
    assert request.record.why  # a card never has an empty reason


def features_binary(tmp_path: Path, features: list[str]) -> str:
    path = tmp_path / "kopicode"
    path.write_text(f"#!/bin/sh\necho '{json.dumps({'features': features})}'\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


async def test_the_probe_reads_the_features(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(kopicode_serve, "_FEATURES", {})
    new = features_binary(tmp_path, ["mcp", "ask.request"])
    assert await serve_supports_ask(new) is True
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    assert await serve_supports_ask(features_binary(old_dir, ["mcp"])) is False
    assert await serve_supports_ask("/no/such/kopicode") is False


@pytest.mark.parametrize(
    ("supported", "timeout", "wired"),
    [(True, 630.0, True), (False, 630.0, False), (True, None, False)],
)
async def test_the_backend_wires_ask_only_when_it_can_be_held(
    monkeypatch: pytest.MonkeyPatch, supported: bool, timeout: float | None, wired: bool
) -> None:
    async def probe(binary: str) -> bool:
        return supported

    monkeypatch.setattr("cuttlefish.agents.kopicode.serve_supports_ask", probe)
    asker = RequestContext(RequestBroker(Journal().append), "p", "t", 600.0).asker(
        role=None, backend="kopicode"
    )
    handler = await KopicodeBackend("kopicode")._ask_handler(asker, timeout)
    assert (handler is not None) is wired
    assert await KopicodeBackend("kopicode")._ask_handler(None, 630.0) is None


async def test_a_long_question_is_cut_with_an_ellipsis_not_silently() -> None:
    broker, _, _ = setup()
    request = broker.raise_question(
        project_id="p1",
        team_id="t1",
        role=None,
        backend="kopicode",
        question="q" * 3000,
        context="c" * 3000,
        window_s=30,
    )
    for text in (request.record.detail, request.record.why):
        assert len(text) == 2000 and text.endswith("…")
