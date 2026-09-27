"""End-to-end: `cuttlefish run --require-approval` and `cuttlefish approve`, wired
together through the real pointer file and a real HTTP round trip (KAN-1711) --
no mock kopicode. Mirrors `test_cli_steering.py`'s own in-process discipline
exactly (`cli._run` driven as a background task on the same event loop while the
test polls for its pointer file, then calls `cli._approve` against it) -- see
that file's own module docstring for `_INVALID_OPENROUTER_KEY`'s reasoning.
Needs outbound network access.
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
async def test_require_approval_alone_still_opens_the_control_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--require-approval` with no `--steerable` still needs the control
    API/pointer file `cuttlefish approve` reaches it through -- the exact gap
    `_run`'s own `needs_control_api = args.steerable or args.require_approval`
    fix closes."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", _INVALID_OPENROUTER_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CUTTLEFISH_LLM_PROVIDER", "replay")

    run_parser = cli.build_parser()
    run_args = run_parser.parse_args(["run", "--require-approval", "add a .gitignore entry"])
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


@pytest.mark.requires_kopicode
async def test_reject_redirects_a_real_running_require_approval_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", _INVALID_OPENROUTER_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CUTTLEFISH_LLM_PROVIDER", "replay")

    run_parser = cli.build_parser()
    run_args = run_parser.parse_args(["run", "--require-approval", "add a .gitignore entry"])
    run_task_coro = asyncio.create_task(cli._run(run_args))

    task_id = None
    try:
        printed = await asyncio.wait_for(_wait_for_first_print(capsys), timeout=5.0)
        task_id = json.loads(printed.strip().splitlines()[0])["task_id"]
        await _wait_for_pointer(task_id)

        reject_parser = cli.build_parser()
        reject_args = reject_parser.parse_args(
            ["approve", task_id, "--reject", "actually add a .dockerignore"]
        )
        exit_code = await asyncio.to_thread(cli._approve, reject_args)
        assert exit_code == cli.EXIT_OK

        # Round 2 needs its own decision too (every round is gated, not just
        # the first) -- poll show's own output until a second DelegationStarted
        # actually lands before approving it.
        async def _wait_for_second_round() -> None:
            while True:
                show_exit = cli.main(["show", task_id])
                assert show_exit == cli.EXIT_OK
                lines = capsys.readouterr().out.strip().splitlines()
                if sum("DelegationStarted" in line for line in lines) >= 2:
                    return
                await asyncio.sleep(0.1)

        await asyncio.wait_for(_wait_for_second_round(), timeout=10.0)

        approve_parser = cli.build_parser()
        approve_args = approve_parser.parse_args(["approve", task_id])
        exit_code = await asyncio.to_thread(cli._approve, approve_args)
        assert exit_code == cli.EXIT_OK

        exit_code = await run_task_coro
    finally:
        if not run_task_coro.done():
            run_task_coro.cancel()

    assert exit_code == cli.EXIT_TASK_FAILED

    show_exit = cli.main(["show", task_id])
    assert show_exit == cli.EXIT_OK
    show_lines = capsys.readouterr().out.strip().splitlines()
    assert sum("DelegationStarted" in line for line in show_lines) == 2
    assert any("actually add a .dockerignore" in line for line in show_lines)


def test_reject_with_an_empty_comment_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    parser = cli.build_parser()
    args = parser.parse_args(["approve", "some-task-id", "--reject", "   "])
    exit_code = cli._approve(args)
    assert exit_code == cli.EXIT_CONFIG_ERROR
    assert "non-empty" in capsys.readouterr().err


def test_approve_with_no_reachable_task_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    parser = cli.build_parser()
    args = parser.parse_args(["approve", "no-such-task"])
    exit_code = cli._approve(args)
    assert exit_code == cli.EXIT_CONFIG_ERROR
    assert "no reachable task" in capsys.readouterr().err
