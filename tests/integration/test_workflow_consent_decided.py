"""Integration: `run_task` journals a `ConsentDecided` event per live consent decision
a delegation's outcome carries (KAN-1792/ADR-0021).

The backend is a stub returning a canned `DelegationOutcome` -- the real workflow, satay
serialization of the outcome across the task boundary, and the real episodic store are
all exercised; only the model-driven part, which needs a live key, is replaced. The
live path is KAN-1794's smoke test.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest
from satay.api.primitives import start
from satay.journal.store import SQLiteStore

from cuttlefish import runtime
from cuttlefish.agents.outcome import ConsentDecisionRecord, DelegationOutcome, ToolCallRecord
from cuttlefish.episodic.events import ConsentDecided, ToolCallRecorded
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.llm.replay import ReplayLlmProvider
from cuttlefish.tasks import delegate
from cuttlefish.workflow import run_task


class _StubBackend:
    NAME = "stub"
    CREDENTIAL_ENV_VARS: tuple[str, ...] = ()

    async def delegate(
        self,
        *,
        task_text: str,
        root: str,
        allow: list[list[str]] | None,
        secrets: Mapping[str, str],
        sandbox_provider: object,
    ) -> DelegationOutcome:
        return DelegationOutcome(
            kind="completed",
            summary="ran ls",
            tool_calls=[ToolCallRecord("run_shell", '{"command":"ls -la"}', "ok")],
            consent_decisions=[
                ConsentDecisionRecord("run_shell", "ls -la", "allow", "allow[0]"),
                ConsentDecisionRecord(
                    "run_shell", "ls && echo done", "deny", "not_a_plain_word_list"
                ),
            ],
        )


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    yield
    runtime.reset()


async def test_each_consent_decision_is_journaled_after_the_tool_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `tasks.delegate` imports the name, so that is the binding to replace; credentials are
    # cleared so a slip here fails closed instead of making a live model call.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(delegate, "resolve_backend", lambda *a, **k: _StubBackend())
    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=episodic_store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary="kopicode",
        )
    )
    root = tmp_path / "scratch"
    root.mkdir()
    handle = start(
        run_task,
        {"task_id": "consent-task", "text": "list files", "root": str(root)},
        run_id="consent-task",
        store=SQLiteStore.open(":memory:"),
    )
    assert (await handle.result())["status"] == "completed"

    payloads = [e.payload for e in episodic_store.read("consent-task")]
    traced = [p for p in payloads if isinstance(p, ToolCallRecorded | ConsentDecided)]
    assert [type(p).__name__ for p in traced] == [
        "ToolCallRecorded",
        "ConsentDecided",
        "ConsentDecided",
    ]
    decided = [p for p in payloads if isinstance(p, ConsentDecided)]
    assert [(d.detail, d.answer, d.rule) for d in decided] == [
        ("ls -la", "allow", "allow[0]"),
        ("ls && echo done", "deny", "not_a_plain_word_list"),
    ]
    episodic_store.close()
