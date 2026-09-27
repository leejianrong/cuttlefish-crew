"""End-to-end: `cuttlefish run --max-tokens`/`--max-cost-usd` (KAN-1712/ADR-0017),
wired together through the real pointer file and a real HTTP round trip -- no
mock kopicode. Mirrors `test_cli_approval.py`'s own in-process discipline
exactly; see that file's own module docstring for `_INVALID_OPENROUTER_KEY`'s
reasoning. Needs outbound network access.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from cuttlefish import cli, runtime
from cuttlefish.steering import steering_pointer_path

_INVALID_OPENROUTER_KEY = "sk-or-v1-" + "0" * 64


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    yield
    runtime.reset()


async def _wait_for_pointer(task_id: str, *, timeout: float = 5.0) -> None:
    async def _poll() -> None:
        while not steering_pointer_path(task_id).exists():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(_poll(), timeout=timeout)


async def _wait_for_first_print(capsys: pytest.CaptureFixture[str]) -> str:
    captured = ""
    while not captured:
        await asyncio.sleep(0.02)
        captured += capsys.readouterr().out
    return captured


@pytest.mark.requires_kopicode
async def test_max_tokens_alone_still_opens_the_control_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--max-tokens` with neither `--steerable` nor `--require-approval` still
    needs the control API/pointer file `cuttlefish approve` reaches it through --
    the exact gap `_run`'s own widened `needs_control_api` closes (mirrors
    `test_cli_approval.py`'s identical `--require-approval`-alone test)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", _INVALID_OPENROUTER_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CUTTLEFISH_LLM_PROVIDER", "replay")

    run_parser = cli.build_parser()
    run_args = run_parser.parse_args(["run", "--max-tokens", "0", "add a .gitignore entry"])
    run_task_coro = asyncio.create_task(cli._run(run_args))

    task_id = None
    try:
        printed = await asyncio.wait_for(_wait_for_first_print(capsys), timeout=5.0)
        first_line = json.loads(printed.strip().splitlines()[0])
        task_id = first_line["task_id"]
        assert "steering" in first_line  # the control API did open

        await _wait_for_pointer(task_id)

        approve_parser = cli.build_parser()
        approve_args = approve_parser.parse_args(["approve", task_id])
        exit_code = await asyncio.to_thread(cli._approve, approve_args)
        assert exit_code == cli.EXIT_OK

        exit_code = await run_task_coro
    finally:
        if not run_task_coro.done():
            run_task_coro.cancel()

    assert exit_code == cli.EXIT_TASK_FAILED  # the real DelegationOutcome kopicode returned

    show_exit = cli.main(["show", task_id])
    assert show_exit == cli.EXIT_OK
    show_lines = capsys.readouterr().out.strip().splitlines()
    assert sum("DelegationStarted" in line for line in show_lines) == 1  # approved, no 2nd round
    assert any("ApprovalDecision" in line for line in show_lines)
