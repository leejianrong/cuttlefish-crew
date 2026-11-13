"""Unit: the ACP spike backend against a local fake ACP agent (CUT-232)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from cuttlefish.agents.acp import AcpBackend
from cuttlefish.agents.acp_client import inside
from cuttlefish.agents.outcome import AuthRequiredError, DelegationError, DelegationOutcome
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


async def test_an_agent_that_needs_auth_is_authenticated_then_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CUTTLEFISH_AGENT_ENV_PASSTHROUGH", "FAKE_ACP_*")
    monkeypatch.setenv("FAKE_ACP_AUTH", "required")
    out = await _run(tmp_path, "hello")  # authenticate was called, then session/new worked
    assert out.kind == "completed"


async def test_an_agent_that_lists_auth_methods_but_is_signed_in_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CUTTLEFISH_AGENT_ENV_PASSTHROUGH", "FAKE_ACP_*")
    monkeypatch.setenv("FAKE_ACP_AUTH", "1")
    assert (await _run(tmp_path, "hello")).kind == "completed"


async def test_an_agent_that_will_not_authenticate_needs_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CUTTLEFISH_AGENT_ENV_PASSTHROUGH", "FAKE_ACP_*")
    monkeypatch.setenv("FAKE_ACP_AUTH", "fail")
    with pytest.raises(AuthRequiredError, match="sign in first"):
        await _run(tmp_path, "hello")


def test_a_path_that_climbs_out_of_the_root_is_not_inside_it(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    assert inside(str(root / "a.txt"), str(root))
    assert not inside(str(root / ".." / "b.txt"), str(root))
    assert not inside("", str(root))


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


def test_a_needs_login_failure_is_named_for_the_dashboard() -> None:
    import satay

    from cuttlefish.team import _failure_kind

    def failed(kind: str) -> BaseException:
        return satay.TaskFailedError("delegate_to_agent_backend", kind, "x")

    assert _failure_kind(failed("AuthRequiredError")) == "needs_login"
    assert _failure_kind(failed("DelegationError")) == "agent_error"
    assert _failure_kind(failed("ValueError")) is None


async def test_an_agent_behind_a_launcher_does_not_hang_the_round(tmp_path: Path) -> None:
    # CUT-266: the npm codex-acp is node around a native binary. The launcher dies, its child keeps
    # the pipes open, and a round used to wait on them forever after the agent had answered.
    launcher = tmp_path / "launcher.py"
    launcher.write_text(f"import subprocess, sys\nsubprocess.run([sys.executable, {FAKE!r}])\n")
    backend = AcpBackend(sys.executable, (str(launcher),), timeout=30.0)
    out = await asyncio.wait_for(_run(tmp_path, "hello", backend=backend), 20)
    assert out.kind == "completed"


async def test_an_agent_reads_and_writes_a_file_inside_the_root_through_the_client(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.txt").write_text("one\ntwo\n")
    read = await _run(tmp_path, f"fsread {tmp_path / 'a.txt'}")
    assert read.summary.startswith('OK({"content": "one\\ntwo\\n"})')
    wrote = await _run(tmp_path, f"fswrite {tmp_path / 'b.txt'}")
    assert (tmp_path / "b.txt").read_text() == "via"
    assert [c.answer for c in wrote.consent_decisions] == ["allow"]


async def test_client_file_methods_refuse_a_path_outside_the_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("s")
    read = await _run(root, f"fsread {tmp_path / 'secret.txt'}")
    assert "ERR(" in read.summary and "secret" not in read.summary.replace("secret.txt", "")
    await _run(root, f"fswrite {tmp_path / 'out.txt'}")
    assert not (tmp_path / "out.txt").exists()
    await _run(root, f"fswrite {root}/../out.txt")
    assert not (tmp_path / "out.txt").exists()


async def test_client_file_writes_are_refused_for_a_read_only_role(tmp_path: Path) -> None:
    out = await _run(tmp_path, f"fswrite {tmp_path / 'b.txt'}", mode="read-only")
    assert "ERR(" in out.summary
    assert not (tmp_path / "b.txt").exists()


async def test_a_client_terminal_runs_an_allowed_command_and_returns_its_output(
    tmp_path: Path,
) -> None:
    out = await _run(tmp_path, "term echo hi", allow=[["echo", "hi"]])
    assert '"output": "hi\\n"' in out.summary
    assert '"exitCode": 0' in out.summary


async def test_a_client_terminal_command_no_rule_allows_is_refused(tmp_path: Path) -> None:
    out = await _run(tmp_path, f"term touch {tmp_path / 'x'}")
    assert "ERR(" in out.summary
    assert not (tmp_path / "x").exists()
    assert [c.answer for c in out.consent_decisions] == ["deny"]


async def test_a_never_allowed_client_terminal_command_is_refused_even_in_auto(
    tmp_path: Path,
) -> None:
    out = await _run(tmp_path, "term sudo rm -rf /", mode="auto")
    assert "ERR(" in out.summary


async def test_a_timed_out_round_carries_the_agents_own_handoff(tmp_path: Path) -> None:
    out = await _run(tmp_path, "hang", timeout=0.5)
    assert out.kind == "failed"
    assert out.handoff is not None and "## Remaining" in out.handoff
    assert "## Done" not in out.summary  # the handoff is not the round's answer


async def test_a_reply_without_the_headings_is_not_a_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ACP_HANDOFF", "none")
    monkeypatch.setenv("CUTTLEFISH_AGENT_ENV_PASSTHROUGH", "FAKE_ACP_HANDOFF")
    out = await _run(tmp_path, "hang", timeout=0.5)
    assert out.kind == "failed"
    assert out.handoff is None
