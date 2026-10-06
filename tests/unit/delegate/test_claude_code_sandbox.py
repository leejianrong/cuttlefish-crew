"""Unit: the sandboxed invocation path shares its argv-building and output
classification with the direct-host path (`build_claude_code_argv`,
`classify_claude_code_output`) -- this only has to prove the seam between
`run_claude_code_in_sandbox` and a `SandboxProvider`, not Claude Code's own
event vocabulary again (already covered by test_claude_code_classify.py).
"""

from __future__ import annotations

import pytest

from cuttlefish.agents.outcome import DelegationError
from cuttlefish.delegate.claude_code import build_claude_code_argv, run_claude_code_in_sandbox
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


def test_build_claude_code_argv_with_no_declared_allowlist_disallows_bash() -> None:
    assert build_claude_code_argv("claude", "add a .gitignore entry") == [
        "claude",
        "-p",
        "add a .gitignore entry",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "acceptEdits",
        "--permission-prompts",
        "none",
        "--disallowedTools",
        "Bash",
    ]


def test_build_claude_code_argv_with_a_declared_allowlist_allows_only_those_commands() -> None:
    argv = build_claude_code_argv(
        "claude", "run the tests", allow=[["go", "test"], ["npm", "test"]]
    )
    allowed = argv[argv.index("--allowedTools") + 1 : argv.index("--disallowedTools")]
    assert allowed == ["Bash(go test:*)", "Bash(npm test:*)"]
    # The never-allowed list rides along as deny patterns, and Bash itself stays available.
    denied = argv[argv.index("--disallowedTools") + 1 :]
    assert "Bash(sudo:*)" in denied
    assert "Bash(git push --force:*)" in denied
    assert "Bash" not in denied


async def test_run_claude_code_in_sandbox_execs_the_right_argv_at_the_right_cwd() -> None:
    result_event = '{"type": "result", "is_error": false, "subtype": "success", "result": "ok"}\n'
    provider = _FakeProvider(ExecResult(exit_code=0, stdout=result_event, stderr=""))
    handle = SandboxHandle(id="sandbox-1")

    outcome = await run_claude_code_in_sandbox(
        provider,  # type: ignore[arg-type]
        handle,
        binary="/usr/local/bin/claude",
        task_text="add a .gitignore entry",
        root="/scratch",
    )

    assert outcome.kind == "completed"
    assert provider.calls == [
        {
            "handle": handle,
            "command": build_claude_code_argv("/usr/local/bin/claude", "add a .gitignore entry"),
            "cwd": "/scratch",
        }
    ]


async def test_run_claude_code_in_sandbox_wraps_a_sandbox_error_as_a_delegation_error() -> None:
    provider = _FakeProvider(SandboxError("container gone"))
    handle = SandboxHandle(id="sandbox-1")

    with pytest.raises(DelegationError, match="container gone"):
        await run_claude_code_in_sandbox(
            provider,  # type: ignore[arg-type]
            handle,
            binary="/usr/local/bin/claude",
            task_text="add a .gitignore entry",
            root="/scratch",
        )


async def test_run_claude_code_in_sandbox_raises_on_output_with_no_result_event() -> None:
    provider = _FakeProvider(ExecResult(exit_code=1, stdout="", stderr="boom"))
    handle = SandboxHandle(id="sandbox-1")

    with pytest.raises(DelegationError, match="no session events"):
        await run_claude_code_in_sandbox(
            provider,  # type: ignore[arg-type]
            handle,
            binary="/usr/local/bin/claude",
            task_text="add a .gitignore entry",
            root="/scratch",
        )
