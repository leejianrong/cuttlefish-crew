"""Unit: the ACP spike backend against a local fake ACP agent (CUT-232)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cuttlefish.agents.acp import AcpBackend, _inside
from cuttlefish.agents.outcome import DelegationError, DelegationOutcome
from cuttlefish.delegate import conversations

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


async def test_a_remembered_session_is_loaded_and_its_replay_is_not_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CUTTLEFISH_RESUME", "1")
    conversations.remember(str(tmp_path), "acp", "builder", "s-old")
    out = await _run(tmp_path, "hello", session="builder")
    assert out.record == "s-old"
    assert out.summary == "ok done"


async def test_without_resume_a_new_session_is_made_and_nothing_is_remembered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CUTTLEFISH_RESUME", raising=False)
    conversations.remember(str(tmp_path), "acp", "builder", "s-old")
    out = await _run(tmp_path, "hello", session="builder")
    assert out.record == "s-new"


async def test_with_resume_the_new_session_id_is_kept_for_the_next_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CUTTLEFISH_RESUME", "1")
    await _run(tmp_path, "hello", session="builder")
    assert conversations.recall(str(tmp_path), "acp", "builder") == "s-new"


async def test_an_edit_is_denied_for_a_read_only_role(tmp_path: Path) -> None:
    out = await _run(tmp_path, "edit", mode="read-only")
    assert out.kind == "refused"
    assert not (tmp_path / "out.txt").exists()


async def test_an_agent_that_wants_auth_is_an_error_not_a_hang(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CUTTLEFISH_AGENT_ENV_PASSTHROUGH", "FAKE_ACP_*")
    monkeypatch.setenv("FAKE_ACP_AUTH", "1")
    with pytest.raises(DelegationError, match="authentication"):
        await _run(tmp_path, "hello")


def test_a_path_that_climbs_out_of_the_root_is_not_inside_it(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    assert _inside(str(root / "a.txt"), str(root))
    assert not _inside(str(root / ".." / "b.txt"), str(root))
    assert not _inside("", str(root))


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
