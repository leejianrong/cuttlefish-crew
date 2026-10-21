"""End-to-end: `cuttlefish run --steerable` and `cuttlefish steer`, wired together
through the real pointer file and a real HTTP round trip (ADR-0008) -- no mock
kopicode. `cli._run`/`cli._steer` are driven directly (as `cli.main` itself does
via `asyncio.run`) rather than via subprocess, matching `test_cli.py`'s own
in-process discipline; `_run` runs as a background task on the same event loop
while the test polls for its pointer file and then calls `_steer` against it,
exactly as a second terminal invocation of the real CLI would.

With no provider credential at all, kopicode fails before ever opening a session (a
raised `DelegationError`) -- steering deliberately never polls after that (ADR-0008's
own boundary). `_INVALID_OPENROUTER_KEY` (mirrors `test_crash_recovery.py`'s own
pattern) is syntactically valid but fake: kopicode opens a real session, sends one
real request, and gets a real (rejected, uncharged) 401 back. Needs outbound network
access.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from cuttlefish import cli, runtime
from cuttlefish.steering import steering_pointer_path

#: See the module docstring -- real session, no real cost, no real credential.
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


@pytest.mark.requires_kopicode
async def test_steer_redirects_a_real_running_steerable_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", _INVALID_OPENROUTER_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    run_parser = cli.build_parser()
    run_args = run_parser.parse_args(["run", "--steerable", "add a .gitignore entry"])
    run_task_coro = asyncio.create_task(cli._run(run_args))

    task_id = None
    try:
        # The task id isn't known until _run prints it -- read it back off stdout
        # once it's actually been printed.
        async def _wait_for_first_print() -> str:
            captured = ""
            while not captured:
                await asyncio.sleep(0.02)
                captured += capsys.readouterr().out
            return captured

        printed = await asyncio.wait_for(_wait_for_first_print(), timeout=5.0)
        first_line = json.loads(printed.strip().splitlines()[0])
        task_id = first_line["task_id"]

        await _wait_for_pointer(task_id)

        steer_parser = cli.build_parser()
        steer_args = steer_parser.parse_args(["steer", task_id, "actually add a .dockerignore"])
        exit_code = await asyncio.to_thread(cli._steer, steer_args)
        assert exit_code == cli.EXIT_OK

        exit_code = await run_task_coro
    finally:
        if not run_task_coro.done():
            run_task_coro.cancel()

    assert exit_code == cli.EXIT_TASK_FAILED  # kopicode has no credential either round

    output_lines = capsys.readouterr().out.strip().splitlines()
    final_result = json.loads(output_lines[-1])
    assert final_result["task_id"] == task_id

    show_exit = cli.main(["show", task_id])
    assert show_exit == cli.EXIT_OK
    show_lines = capsys.readouterr().out.strip().splitlines()
    assert sum("DelegationStarted" in line for line in show_lines) == 2
    assert any("actually add a .dockerignore" in line for line in show_lines)
