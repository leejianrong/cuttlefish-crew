"""Live: kopicode's real model asks for consent and cuttlefish's policy answers (KAN-1794).

The one thing the free tests cannot show: a real model, a real ``consent.request`` for a real
shell command, and the policy's allow and deny reaching kopicode's own ``permission_decided``
events. The role may run ``ls`` and nothing else, so ``ls -la`` is allowed and
``ls && echo done`` (shell syntax) must be denied. Costs cents per run, and only runs with a
real key in the environment (``requires_live_credential``). Run once on the host and once
inside a container sandbox (KAN-1793).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cuttlefish.agents.kopicode import KopicodeBackend
from cuttlefish.agents.outcome import DelegationOutcome
from cuttlefish.delegate.consent import ConsentPolicy
from cuttlefish.delegate.kopicode_serve import run_kopicode_serve
from cuttlefish.sandbox.container import ContainerSandboxProvider

_TASK = (
    "Use the run_shell tool to run exactly this command: ls -la\n"
    "Then use the run_shell tool to run exactly this command: ls && echo done\n"
    "Run both even if one is refused. Do not edit any file. Then say you are finished."
)


def _repo(path: Path) -> None:
    env = {"PATH": "/usr/bin:/bin"}
    subprocess.run(["git", "init", "-q", str(path)], check=True, env=env, cwd=path)
    (path / "README.md").write_text("a scratch repo for the live consent test\n")


def _assert_both_decisions_were_made_and_journalable(outcome: DelegationOutcome) -> None:
    # kopicode's `detail` for a shell command is the argv it will run, space-joined.
    by_detail = {d.detail: d for d in outcome.consent_decisions}
    allowed = by_detail.get("/bin/sh -c ls -la")
    denied = by_detail.get("/bin/sh -c ls && echo done")
    assert allowed is not None, outcome.consent_decisions
    assert (allowed.answer, allowed.rule) == ("allow", "allow:ls")
    assert denied is not None, outcome.consent_decisions
    assert (denied.answer, denied.rule) == ("deny", "not_a_plain_word_list")
    # kopicode's own journal agrees: its remote-sourced decisions became tool-call statuses.
    statuses = {c.detail: c.status for c in outcome.tool_calls if c.tool == "run_shell"}
    assert any("ls -la" in d and s == "ok" for d, s in statuses.items()), outcome.tool_calls
    assert any("&&" in d and s == "denied" for d, s in statuses.items()), outcome.tool_calls


@pytest.mark.requires_kopicode
@pytest.mark.requires_live_credential
async def test_a_real_model_is_allowed_ls_and_denied_shell_syntax(tmp_path: Path) -> None:
    _repo(tmp_path)
    outcome = await run_kopicode_serve(
        binary="kopicode",
        task_text=_TASK,
        root=str(tmp_path),
        policy=ConsentPolicy([["ls"]]),
        env={"OPENROUTER_API_KEY": os.environ["OPENROUTER_API_KEY"]},
        timeout=300,
    )
    _assert_both_decisions_were_made_and_journalable(outcome)


@pytest.mark.requires_kopicode
@pytest.mark.requires_docker
@pytest.mark.requires_live_credential
async def test_the_same_holds_inside_a_container_sandbox(tmp_path: Path) -> None:
    _repo(tmp_path)
    outcome = await KopicodeBackend("kopicode").delegate(
        task_text=_TASK,
        root=str(tmp_path),
        allow=[["ls"]],
        secrets={"OPENROUTER_API_KEY": os.environ["OPENROUTER_API_KEY"]},
        sandbox_provider=ContainerSandboxProvider(),
    )
    _assert_both_decisions_were_made_and_journalable(outcome)
