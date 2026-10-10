"""Unit: Claude Code driven over stream-json with its permission requests answered live (V4-K)
against the scripted fake -- which commands are approved, who is asked, what is journaled, how a
round ends."""

from __future__ import annotations

import asyncio
import json
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from cuttlefish.agents.claude_code import ClaudeCodeBackend
from cuttlefish.agents.outcome import DelegationError, DelegationOutcome
from cuttlefish.delegate.claude_code_live import build_live_argv, run_claude_code_live
from cuttlefish.episodic.events import EventPayload
from cuttlefish.episodic.store import EpisodicEvent
from cuttlefish.requests import RequestBroker, RequestContext

FAKE = Path(__file__).with_name("fake_claude_stream.py")
ALLOW = [["uv", "run", "pytest"]]


class Fake:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "project"
        self.root.mkdir()
        self.scenario = tmp_path / "scenario.json"
        self.log = tmp_path / "log"
        self.log.touch()
        self.binary = tmp_path / "claude"

    def script(self, scenario: dict[str, Any]) -> ClaudeCodeBackend:
        self.scenario.write_text(json.dumps(scenario))
        self.binary.write_text(
            f'#!/bin/sh\nexec {sys.executable} {FAKE} {self.scenario} {self.log} "$@"\n'
        )
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        return ClaudeCodeBackend(str(self.binary), transport="stdio")

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def decisions(self) -> list[str]:
        return [
            m["response"]["response"]["behavior"]
            for m in self.lines()
            if m.get("type") == "control_response"
        ]

    async def run(self, backend: ClaudeCodeBackend, **kwargs: Any) -> DelegationOutcome:
        return await backend.delegate(
            task_text="do it",
            root=str(self.root),
            allow=kwargs.pop("allow", ALLOW),
            secrets={},
            sandbox_provider=None,
            **kwargs,
        )


@pytest.fixture
def fake(tmp_path: Path) -> Fake:
    return Fake(tmp_path)


def test_the_live_argv_turns_the_protocol_on_and_passes_no_allow_patterns() -> None:
    argv = build_live_argv("claude")
    assert argv[argv.index("--permission-prompt-tool") + 1] == "stdio"
    assert argv[argv.index("--permission-prompts") + 1] == "host"
    assert "--allowedTools" not in argv
    assert argv[argv.index("--permission-mode") + 1] == "manual"
    assert "Bash(sudo:*)" in argv
    assert "Write" not in argv
    auto = build_live_argv("claude", mode="auto")
    assert "--allowedTools" not in auto
    assert "Bash(curl:*)" in auto
    assert "Write" in build_live_argv("claude", mode="read-only")


async def test_an_allowed_command_is_accepted_and_a_refused_one_denied(fake: Fake) -> None:
    backend = fake.script(
        {
            "steps": [
                {"bash": "uv run pytest -q"},
                {"bash": "sudo rm -rf /"},
                {"bash": "uv run pytest && ls"},
            ]
        }
    )
    outcome = await fake.run(backend)
    assert fake.decisions() == ["allow", "deny", "deny"]
    assert [c.answer for c in outcome.consent_decisions] == ["allow", "deny", "deny"]
    assert [t.status for t in outcome.tool_calls] == ["ok", "denied", "denied"]
    assert outcome.kind == "completed"
    assert outcome.tokens == 15
    assert outcome.cost_usd == 0.01


async def test_a_round_whose_every_call_was_declined_is_refused(fake: Fake) -> None:
    backend = fake.script({"steps": [{"bash": "make build"}]})
    outcome = await fake.run(backend)
    assert outcome.kind == "refused"
    assert "make build" in (outcome.reason or "")
    assert "no_matching_allow_entry" in (outcome.reason or "")


async def test_a_declined_command_does_not_hide_an_edit_that_landed(fake: Fake) -> None:
    backend = fake.script({"steps": [{"write": str(fake.root / "a.py")}, {"bash": "make build"}]})
    outcome = await fake.run(backend)
    assert outcome.kind == "completed"
    assert outcome.edited_paths == ["a.py"]
    assert [t.status for t in outcome.tool_calls] == ["ok", "denied"]


async def test_auto_allows_anything_not_never_allowed(fake: Fake) -> None:
    backend = fake.script(
        {"steps": [{"bash": "make build && make test"}, {"bash": "git push --force"}]}
    )
    await fake.run(backend, mode="auto", allow=[])
    assert fake.decisions() == ["allow", "deny"]
    assert "--allowedTools" not in next(m["argv"] for m in fake.lines() if "argv" in m)


async def test_an_edit_inside_the_root_is_accepted_and_one_outside_denied(
    fake: Fake, tmp_path: Path
) -> None:
    inside_path = str(fake.root / "a" / "b.py")
    backend = fake.script(
        {"steps": [{"write": inside_path}, {"write": str(tmp_path / "elsewhere.txt")}]}
    )
    outcome = await fake.run(backend)
    assert fake.decisions() == ["allow", "deny"]
    assert outcome.edited_paths == ["a/b.py"]
    assert outcome.consent_decisions[1].rule == "edit_outside_root"


async def test_no_edit_is_accepted_in_a_read_only_role(fake: Fake) -> None:
    backend = fake.script({"steps": [{"write": str(fake.root / "a.py")}]})
    outcome = await fake.run(backend, mode="read-only", allow=[])
    assert fake.decisions() == ["deny"]
    assert outcome.kind == "refused"
    assert outcome.edited_paths == []


async def test_any_other_tool_that_prompts_is_denied(fake: Fake) -> None:
    backend = fake.script({"steps": [{"tool": "WebFetch"}]})
    outcome = await fake.run(backend)
    assert fake.decisions() == ["deny"]
    assert outcome.consent_decisions[0].rule == "tool_not_granted"


async def test_a_timed_out_round_is_interrupted_and_says_so(fake: Fake) -> None:
    fake.script({"steps": [{"hang": True}]})
    outcome = await run_claude_code_live(
        binary=str(fake.binary),
        task_text="x",
        root=str(fake.root),
        decide=lambda kind, detail: None,  # type: ignore[arg-type,return-value]
        timeout=0.3,
    )
    assert outcome.kind == "failed"
    assert outcome.failure_kind == "round_timeout"
    assert any(m.get("request", {}).get("subtype") == "interrupt" for m in fake.lines())


async def test_a_process_that_dies_is_a_delegation_error_with_its_stderr(fake: Fake) -> None:
    backend = fake.script({"crash": True})
    with pytest.raises(DelegationError, match="boom"):
        await fake.run(backend)


async def test_a_missing_binary_is_a_delegation_error(tmp_path: Path) -> None:
    backend = ClaudeCodeBackend(str(tmp_path / "nope"), transport="stdio")
    with pytest.raises(DelegationError, match="not found"):
        await backend.delegate(
            task_text="x", root=str(tmp_path), allow=[], secrets={}, sandbox_provider=None
        )


class Journal:
    def __init__(self) -> None:
        self.payloads: list[EventPayload] = []

    def append(self, task_id: str, payload: EventPayload) -> EpisodicEvent:
        self.payloads.append(payload)
        return EpisodicEvent(task_id, len(self.payloads), 1, datetime.now(UTC), payload)


def _asker(broker: RequestBroker) -> Any:
    return RequestContext(broker, "p1", "t1", 30.0).asker(role="builder", backend="claude-code")


async def test_an_unlisted_command_waits_for_a_person_and_their_answer_reaches_claude(
    fake: Fake,
) -> None:
    broker = RequestBroker(Journal().append)
    backend = fake.script({"steps": [{"bash": "make lint"}]})
    run = asyncio.create_task(fake.run(backend, asker=_asker(broker)))
    for _ in range(200):
        if broker.pending():
            break
        await asyncio.sleep(0.05)
    (request,) = broker.pending()
    assert request.record.backend == "claude-code"
    assert not run.done()
    broker.answer(request.id, "allow_once")
    outcome = await run
    assert fake.decisions() == ["allow"]
    assert outcome.consent_decisions == []


async def test_a_question_goes_to_a_person_and_their_words_reach_the_model(fake: Fake) -> None:
    broker = RequestBroker(Journal().append)
    backend = fake.script(
        {"steps": [{"ask": [{"question": "Which color?", "options": ["Red", "Blue"]}]}]}
    )
    run = asyncio.create_task(fake.run(backend, asker=_asker(broker)))
    for _ in range(200):
        if broker.pending():
            break
        await asyncio.sleep(0.05)
    (request,) = broker.pending()
    assert request.record.kind == "question"
    assert request.record.options == ["Red", "Blue"]
    broker.answer(request.id, "answer", text="Teal")
    outcome = await run
    reply = next(m for m in fake.lines() if m.get("type") == "control_response")
    assert reply["response"]["response"]["updatedInput"]["answers"] == {"Which color?": "Teal"}
    assert outcome.kind == "completed"


async def test_a_question_with_nobody_to_ask_is_denied(fake: Fake) -> None:
    backend = fake.script({"steps": [{"ask": [{"question": "Which?", "options": ["a"]}]}]})
    await fake.run(backend)
    assert fake.decisions() == ["deny"]
