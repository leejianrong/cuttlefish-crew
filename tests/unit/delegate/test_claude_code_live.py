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
from cuttlefish.broker import Lease
from cuttlefish.delegate.claude_code_live import build_live_argv, run_claude_code_live
from cuttlefish.delegate.consent import ConsentPolicy
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


async def test_a_lease_replaces_the_key_and_points_claude_at_the_broker(fake: Fake) -> None:
    lease = Lease(
        token="cfb_placeholder",
        upstream="anthropic",
        base="https://example.invalid",
        header="x-api-key",
        url="http://127.0.0.1:1/anthropic/cfb_placeholder",
        key="real-key-never-given",
    )
    backend = fake.script({"steps": []})
    await fake.run(backend, lease=lease)
    # A declared key beats the environment, so give it one to prove the lease beats that too.
    await backend.delegate(
        task_text="do it",
        root=str(fake.root),
        allow=ALLOW,
        secrets={"ANTHROPIC_API_KEY": "real-key-never-given", "GITHUB_TOKEN": "gh-value"},
        sandbox_provider=None,
        lease=lease,
    )
    envs = [m["env"] for m in fake.lines() if "env" in m]
    assert len(envs) == 2
    for env in envs:
        assert env["ANTHROPIC_BASE_URL"] == lease.url
        assert env["ANTHROPIC_API_KEY"] == "cfb_placeholder"
    assert "real-key-never-given" not in fake.log.read_text()
    assert envs[1]["GITHUB_TOKEN"] == "gh-value"  # a project secret still arrives


def _argv(fake: Fake) -> list[str]:
    return [m["argv"] for m in fake.lines() if "argv" in m][-1]


def _claude_has(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_id: str) -> None:
    """What Claude Code leaves behind after a session: its transcript, under some folder."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    folder = tmp_path / "claude-config" / "projects" / "-some-folder"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{session_id}.jsonl").write_text("{}\n")


async def test_a_later_round_resumes_the_remembered_session(
    fake: Fake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cuttlefish.delegate import conversations

    monkeypatch.setenv("CUTTLEFISH_RESUME", "1")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    backend = fake.script({})
    await fake.run(backend, session="dev")
    first = _argv(fake)
    assert first[first.index("--session-id") + 1] == conversations.recall(
        str(fake.root), "claude-code", "dev"
    )
    session_id = first[first.index("--session-id") + 1]
    _claude_has(tmp_path, monkeypatch, session_id)
    fake.log.write_text("")
    await fake.run(backend, session="dev")
    second = _argv(fake)
    assert second[second.index("--resume") + 1] == session_id
    assert "--session-id" not in second


async def test_a_session_claude_no_longer_has_is_replaced(
    fake: Fake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cuttlefish.delegate import conversations

    monkeypatch.setenv("CUTTLEFISH_RESUME", "1")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    conversations.remember(str(fake.root), "claude-code", "dev", "gone")
    await fake.run(fake.script({}), session="dev")
    argv = _argv(fake)
    assert "--resume" not in argv
    assert argv[argv.index("--session-id") + 1] != "gone"
    assert conversations.recall(str(fake.root), "claude-code", "dev") != "gone"


async def test_another_role_does_not_share_the_session(
    fake: Fake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cuttlefish.delegate import conversations

    monkeypatch.setenv("CUTTLEFISH_RESUME", "1")
    backend = fake.script({})
    await fake.run(backend, session="dev")
    dev = conversations.recall(str(fake.root), "claude-code", "dev")
    assert dev
    _claude_has(tmp_path, monkeypatch, dev)
    fake.log.write_text("")
    await fake.run(backend, session="reviewer")
    assert "--resume" not in _argv(fake)


async def test_without_the_switch_every_round_is_a_new_session(
    fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CUTTLEFISH_RESUME", raising=False)
    await fake.run(fake.script({}), session="dev")
    argv = _argv(fake)
    assert "--resume" not in argv and "--session-id" not in argv
    assert not (fake.root / ".cuttlefish" / "conversations.json").exists()


def test_session_exists_looks_in_every_project_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cuttlefish.delegate.claude_code_live import session_exists

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert not session_exists("abc")
    _claude_has(tmp_path, monkeypatch, "abc")
    assert session_exists("abc") and not session_exists("abd")


DOCUMENT = (
    "## Done\n- notes.md written\n\n## Remaining\n- the last paragraph\n\n"
    "## Failed or refused\nnothing\n\n## Open questions\nnone"
)


async def _timed_out_round(fake: Fake, **scenario: Any) -> DelegationOutcome:
    fake.script({"steps": [{"hang": True}], **scenario})
    return await run_claude_code_live(
        binary=str(fake.binary),
        task_text="x",
        root=str(fake.root),
        decide=ConsentPolicy().decide,  # nothing is allowed
        timeout=0.3,
        handoff_goal="The next session carries on with this work: x",
    )


async def test_a_round_that_ran_out_of_time_is_asked_for_its_handoff(fake: Fake) -> None:
    # CUT-140: another user message on the same process; tools are denied; the reply is the
    # document.
    outcome = await _timed_out_round(fake, handoff=DOCUMENT, handoff_bash="make lint")
    assert outcome.failure_kind == "round_timeout"
    assert outcome.handoff == DOCUMENT
    users = [m for m in fake.lines() if m.get("type") == "user" and "message" in m]
    assert len(users) == 2
    prompt = users[1]["message"]["content"]
    assert "Open questions" in prompt and "carries on with this work" in prompt
    # The first turn's own pending command and the handoff turn's are both denied; only the first
    # is the round's, so the handoff turn's refusal is not journaled as a consent decision.
    assert fake.decisions() == ["deny", "deny"]
    assert [c.detail for c in outcome.consent_decisions] == ["/bin/sh -c sleep 100"]
    assert outcome.tokens == 15 + 28  # the asking is counted
    assert outcome.cost_usd == pytest.approx(0.03)


async def test_a_reply_without_the_sections_is_not_a_handoff_for_claude_code(fake: Fake) -> None:
    outcome = await _timed_out_round(fake, handoff="Sure, I will carry on tomorrow.")
    assert outcome.failure_kind == "round_timeout"
    assert outcome.handoff is None


async def test_a_finished_round_is_not_asked_for_a_handoff(fake: Fake) -> None:
    fake.script({"handoff": DOCUMENT})
    outcome = await run_claude_code_live(
        binary=str(fake.binary),
        task_text="x",
        root=str(fake.root),
        decide=lambda kind, detail: None,  # type: ignore[arg-type,return-value]
        handoff_goal="carry on",
    )
    assert outcome.kind == "completed" and outcome.handoff is None
    assert len([m for m in fake.lines() if m.get("type") == "user" and "message" in m]) == 1
