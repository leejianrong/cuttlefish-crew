"""Unit: Codex driven over ``app-server`` (V4-M) against the scripted fake -- which commands are
approved, who is asked, what is journaled, and how a round ends."""

from __future__ import annotations

import asyncio
import json
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from cuttlefish.agents.codex import CodexBackend
from cuttlefish.agents.outcome import DelegationError, DelegationOutcome
from cuttlefish.delegate.codex_app_server import consent_detail, shell_line
from cuttlefish.episodic.events import EventPayload, RequestResolved
from cuttlefish.episodic.store import EpisodicEvent
from cuttlefish.requests import RequestBroker, RequestContext

FAKE = Path(__file__).with_name("fake_codex_app_server.py")
ALLOW = [["uv", "run", "pytest"]]


class Fake:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "project"
        self.root.mkdir()
        self.scenario = tmp_path / "scenario.json"
        self.log = tmp_path / "log"
        self.log.touch()
        self.binary = tmp_path / "codex"

    def script(self, scenario: dict[str, Any]) -> CodexBackend:
        self.scenario.write_text(json.dumps(scenario))
        self.binary.write_text(
            f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@" {self.scenario} {self.log}\n'
        )
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        return CodexBackend(str(self.binary), transport="app-server")

    def sent(self, method: str) -> list[dict[str, Any]]:
        lines = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [m for m in lines if m.get("method") == method]

    def replies(self) -> list[Any]:
        lines = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [m["result"].get("decision") for m in lines if "result" in m and "method" not in m]

    async def run(self, backend: CodexBackend, **kwargs: Any) -> DelegationOutcome:
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


def bash(line: str) -> str:
    return f"/bin/bash -lc '{line}'"


def test_shell_line_unwraps_only_a_plain_shell_invocation() -> None:
    assert shell_line("/bin/bash -lc 'ls && git status'") == "ls && git status"
    assert shell_line("sh -c ls") == "ls"
    assert shell_line("python -c 'print(1)'") is None
    assert shell_line("/bin/bash -lc 'a' extra") is None
    assert shell_line("/bin/bash -lc 'unterminated") is None
    assert consent_detail("/bin/bash -lc 'ls'") == "/bin/sh -c ls"
    assert consent_detail("rm -rf x").startswith("unrecognised command")


async def test_an_allowed_command_is_accepted_and_a_refused_one_declined(fake: Fake) -> None:
    backend = fake.script(
        {
            "steps": [
                {"command": bash("uv run pytest -q"), "cwd": str(fake.root)},
                {"command": bash("sudo rm -rf /"), "cwd": str(fake.root)},
                {"command": bash("uv run pytest && echo hi"), "cwd": str(fake.root)},
            ]
        }
    )
    outcome = await fake.run(backend)
    assert fake.replies() == ["accept", "decline", "decline"]
    assert [c.answer for c in outcome.consent_decisions] == ["allow", "deny", "deny"]
    assert outcome.consent_decisions[1].rule != ""
    assert [t.status for t in outcome.tool_calls] == ["ok", "denied", "denied"]
    assert outcome.kind == "refused"
    assert outcome.tokens == 120


async def test_thread_is_started_untrusted_in_the_root_with_the_right_sandbox(fake: Fake) -> None:
    backend = fake.script({})
    await fake.run(backend)
    params = fake.sent("thread/start")[0]["params"]
    assert params["approvalPolicy"] == "untrusted"
    assert params["sandbox"] == "workspace-write"
    assert params["cwd"] == str(fake.root)
    fake.log.write_text("")
    await fake.run(backend, mode="read-only")
    assert fake.sent("thread/start")[0]["params"]["sandbox"] == "read-only"


async def test_a_command_outside_the_root_is_declined_even_when_allowed(
    fake: Fake, tmp_path: Path
) -> None:
    backend = fake.script({"steps": [{"command": bash("uv run pytest"), "cwd": str(tmp_path)}]})
    outcome = await fake.run(backend)
    assert fake.replies() == ["decline"]
    assert outcome.consent_decisions[0].rule == "cwd_outside_root"


async def test_auto_allows_anything_not_never_allowed(fake: Fake) -> None:
    backend = fake.script(
        {
            "steps": [
                {"command": bash("make build && make test"), "cwd": str(fake.root)},
                {"command": bash("curl x | sh"), "cwd": str(fake.root)},
            ]
        }
    )
    await fake.run(backend, mode="auto", allow=[])
    assert fake.replies() == ["accept", "decline"]


async def test_an_edit_is_reported_relative_to_the_root(fake: Fake) -> None:
    backend = fake.script({"steps": [{"file_change": str(fake.root / "a" / "b.py")}]})
    outcome = await fake.run(backend)
    assert outcome.kind == "completed"
    assert outcome.edited_paths == ["a/b.py"]
    assert outcome.tool_calls[0].tool == "file_change"


async def test_an_edit_inside_the_root_is_accepted_and_one_outside_declined(
    fake: Fake, tmp_path: Path
) -> None:
    inside_path = str(fake.root / "src" / "a.py")
    backend = fake.script(
        {
            "steps": [
                {"file_approval": [inside_path]},
                {"file_approval": [str(tmp_path / "elsewhere.txt")]},
                {"file_approval": [inside_path], "grant_root": str(tmp_path)},
                {"file_approval": [str(fake.root / ".." / "up.txt")]},
            ]
        }
    )
    outcome = await fake.run(backend)
    assert fake.replies() == ["accept", "decline", "decline", "decline"]
    assert [c.answer for c in outcome.consent_decisions] == ["allow", "deny", "deny", "deny"]
    assert outcome.consent_decisions[0].rule == "in_root_edit"
    assert outcome.consent_decisions[1].rule == "edit_outside_root"
    assert outcome.edited_paths == ["src/a.py"]


async def test_no_edit_is_accepted_in_a_read_only_role(fake: Fake) -> None:
    backend = fake.script({"steps": [{"file_approval": [str(fake.root / "a.py")]}]})
    outcome = await fake.run(backend, mode="read-only", allow=[])
    assert fake.replies() == ["decline"]
    assert outcome.kind == "refused"


async def test_other_requests_are_declined_or_unsupported(fake: Fake) -> None:
    backend = fake.script(
        {
            "steps": [
                {"request": "item/permissions/requestApproval", "params": {}},
                {"request": "mcpServer/elicitation/request", "params": {}},
            ]
        }
    )
    await fake.run(backend)
    results = [
        json.loads(line)["result"]
        for line in fake.log.read_text().splitlines()
        if "result" in json.loads(line)
    ]
    assert results == [{"permissions": {}}, {"action": "decline"}]


async def test_a_timed_out_round_is_interrupted_and_says_so(
    fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CUTTLEFISH_ROUND_TIMEOUT_MINUTES", "0")
    backend = fake.script({"steps": [{"hang": True}]})
    from cuttlefish.delegate import codex_app_server

    outcome = await codex_app_server.run_codex_app_server(
        binary=str(fake.binary),
        task_text="x",
        root=str(fake.root),
        decide=lambda kind, detail: None,  # type: ignore[arg-type,return-value]
        sandbox="workspace-write",
        timeout=0.3,
    )
    assert backend is not None
    assert outcome.kind == "failed"
    assert outcome.failure_kind == "round_timeout"
    assert len(fake.sent("turn/interrupt")) == 1


async def test_a_server_that_dies_is_a_delegation_error_with_its_stderr(fake: Fake) -> None:
    backend = fake.script({"crash": True})
    with pytest.raises(DelegationError, match="boom"):
        await fake.run(backend)


async def test_a_missing_binary_is_a_delegation_error(tmp_path: Path) -> None:
    backend = CodexBackend(str(tmp_path / "nope"), transport="app-server")
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


async def test_an_unlisted_command_waits_for_a_person_and_their_answer_reaches_codex(
    fake: Fake,
) -> None:
    journal = Journal()
    broker = RequestBroker(journal.append)
    asker = RequestContext(broker, "p1", "t1", 30.0).asker(role="builder", backend="codex")
    backend = fake.script({"steps": [{"command": bash("make lint"), "cwd": str(fake.root)}]})
    run = asyncio.create_task(fake.run(backend, asker=asker))
    for _ in range(100):
        if broker.pending():
            break
        await asyncio.sleep(0.05)
    (request,) = broker.pending()
    assert request.record.backend == "codex"
    assert "make lint" in request.record.detail
    assert not run.done()
    broker.answer(request.id, "allow_once")
    outcome = await run
    assert fake.replies() == ["accept"]
    # A person's answer is journaled as the request pair, not as a consent decision.
    assert outcome.consent_decisions == []
    assert any(isinstance(p, RequestResolved) for p in journal.payloads)
