"""Unit: the sandboxed invocation path shares its argv-building and output
classification with the direct-host path (`build_codex_argv`,
`classify_codex_output`) -- this only has to prove the seam between
`run_codex_in_sandbox` and a `SandboxProvider`, not Codex's own event
vocabulary again (already covered by test_codex_classify.py).
"""

from __future__ import annotations

import pytest

from cuttlefish.agents.outcome import DelegationError
from cuttlefish.delegate.codex import build_codex_argv, run_codex_in_sandbox
from cuttlefish.sandbox.provider import ExecResult, SandboxError, SandboxHandle


class _FakeProvider:
    def __init__(self, result: ExecResult | Exception) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []

    async def exec(
        self,
        handle: SandboxHandle,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        self.calls.append({"handle": handle, "command": list(command), "cwd": cwd})
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def test_build_codex_argv_with_no_declared_allowlist_uses_read_only_sandbox() -> None:
    assert build_codex_argv("codex", "add a .gitignore entry") == [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "add a .gitignore entry",
    ]


def test_build_codex_argv_with_a_declared_allowlist_uses_workspace_write_sandbox() -> None:
    argv = build_codex_argv("codex", "run the tests", allow=[["go", "test"]])
    assert argv[-3:] == ["--sandbox", "workspace-write", "run the tests"]


async def test_run_codex_in_sandbox_execs_the_right_argv_at_the_right_cwd() -> None:
    turn_completed = (
        '{"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}}\n'
    )
    provider = _FakeProvider(ExecResult(exit_code=0, stdout=turn_completed, stderr=""))
    handle = SandboxHandle(id="sandbox-1")

    outcome = await run_codex_in_sandbox(
        provider,  # type: ignore[arg-type]
        handle,
        binary="/usr/local/bin/codex",
        task_text="add a .gitignore entry",
        root="/scratch",
    )

    assert outcome.kind == "completed"
    assert provider.calls == [
        {
            "handle": handle,
            "command": build_codex_argv("/usr/local/bin/codex", "add a .gitignore entry"),
            "cwd": "/scratch",
        }
    ]


async def test_run_codex_in_sandbox_wraps_a_sandbox_error_as_a_delegation_error() -> None:
    provider = _FakeProvider(SandboxError("container gone"))
    handle = SandboxHandle(id="sandbox-1")

    with pytest.raises(DelegationError, match="container gone"):
        await run_codex_in_sandbox(
            provider,  # type: ignore[arg-type]
            handle,
            binary="/usr/local/bin/codex",
            task_text="add a .gitignore entry",
            root="/scratch",
        )


async def test_run_codex_in_sandbox_raises_on_output_with_no_terminal_event() -> None:
    provider = _FakeProvider(ExecResult(exit_code=1, stdout="", stderr="boom"))
    handle = SandboxHandle(id="sandbox-1")

    with pytest.raises(DelegationError, match="no session events"):
        await run_codex_in_sandbox(
            provider,  # type: ignore[arg-type]
            handle,
            binary="/usr/local/bin/codex",
            task_text="add a .gitignore entry",
            root="/scratch",
        )
