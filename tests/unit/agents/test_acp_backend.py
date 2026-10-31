"""Unit: the ACP spike backend against a local fake ACP agent (CUT-232)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cuttlefish.agents.acp import AcpBackend
from cuttlefish.agents.outcome import DelegationError, DelegationOutcome

FAKE = str(Path(__file__).parents[2] / "fixtures" / "fake_acp_agent.py")


async def _run(
    tmp_path: Path,
    task: str,
    *,
    allow: list[list[str]] | None = None,
    mode: str = "standard",
    session: str | None = None,
    timeout: float = 30.0,
    backend: AcpBackend | None = None,
) -> DelegationOutcome:
    backend = backend or AcpBackend(sys.executable, (FAKE,), timeout=timeout)
    return await backend.delegate(
        task_text=task,
        root=str(tmp_path),
        allow=allow,
        secrets={},
        sandbox_provider=None,
        mode=mode,
        session=session,
    )


async def test_a_plain_turn_streams_text_usage_and_the_session_id(tmp_path: Path) -> None:
    out = await _run(tmp_path, "hello")
    assert out.kind == "completed"
    assert out.summary == "ok done"
    assert out.record == "s-new"
    assert (out.tokens, out.cost_usd) == (42, 0.01)


async def test_an_edit_inside_the_root_is_allowed(tmp_path: Path) -> None:
    out = await _run(tmp_path, "edit")
    assert out.kind == "completed"
    assert out.edited_paths == [str(tmp_path / "out.txt")]
    assert (tmp_path / "out.txt").read_text() == "hi"
    assert [c.status for c in out.tool_calls] == ["ok"]


async def test_a_command_no_rule_allows_is_rejected_with_the_agents_reject_option(
    tmp_path: Path,
) -> None:
    out = await _run(tmp_path, "run make build")
    assert out.kind == "refused"
    assert out.consent_decisions[0].answer == "deny"
    assert out.tool_calls[0].status == "denied"


async def test_a_command_the_allowlist_names_is_allowed(tmp_path: Path) -> None:
    out = await _run(tmp_path, "run make build", allow=[["make", "build"]])
    assert out.kind == "completed"
    assert out.consent_decisions[0].answer == "allow"


async def test_a_never_allowed_command_is_denied_even_in_auto(tmp_path: Path) -> None:
    out = await _run(tmp_path, "run sudo rm -rf /", mode="auto")
    assert out.kind == "refused"


async def test_a_timeout_sends_session_cancel_and_fails(tmp_path: Path) -> None:
    out = await _run(tmp_path, "hang", timeout=0.5)
    assert out.kind == "failed"
    assert out.reason == "timeout"


async def test_a_known_session_is_loaded_and_its_replay_is_not_the_answer(tmp_path: Path) -> None:
    out = await _run(tmp_path, "hello", session="s-old")
    assert out.record == "s-old"
    assert out.summary == "ok done"


async def test_an_agent_that_wants_auth_is_an_error_not_a_hang(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CUTTLEFISH_AGENT_ENV_PASSTHROUGH", "FAKE_ACP_*")
    monkeypatch.setenv("FAKE_ACP_AUTH", "1")
    with pytest.raises(DelegationError, match="authentication"):
        await _run(tmp_path, "hello")


async def test_a_missing_binary_is_a_delegation_error(tmp_path: Path) -> None:
    with pytest.raises(DelegationError):
        await _run(tmp_path, "x", backend=AcpBackend("no-such-acp-agent-binary"))


async def test_it_refuses_a_sandbox_provider(tmp_path: Path) -> None:
    with pytest.raises(DelegationError):
        await AcpBackend(sys.executable).delegate(
            task_text="x",
            root=str(tmp_path),
            allow=None,
            secrets={},
            sandbox_provider=object(),  # type: ignore[arg-type]
        )
