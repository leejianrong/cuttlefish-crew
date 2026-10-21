"""Unit: `cuttlefish.runtime`'s `ContextVar`-backed configuration (ADR-0009, Q49).

The concrete regression this exists to prevent: a daemon driving several projects'
teams concurrently in one process must never let one project's `configure()` call
leak into a sibling project's `current()` read.
"""

from __future__ import annotations

import asyncio

import pytest

from cuttlefish import runtime


def _runtime(kopicode_binary: str) -> runtime.Runtime:
    from unittest.mock import MagicMock

    return runtime.Runtime(
        episodic_store=MagicMock(),
        kopicode_binary=kopicode_binary,
    )


@pytest.fixture(autouse=True)
def _reset() -> None:
    yield
    runtime.reset()


def test_current_raises_before_configure() -> None:
    with pytest.raises(RuntimeError):
        runtime.current()


def test_configure_then_current_round_trips() -> None:
    runtime.configure(_runtime("kopicode-a"))
    assert runtime.current().kopicode_binary == "kopicode-a"


async def test_two_concurrent_tasks_each_see_only_their_own_runtime() -> None:
    """The regression `ContextVar` (not a plain global) exists to prevent: two
    `asyncio.create_task`s, each calling `configure()` before any further `await`,
    must never observe each other's value from `current()`."""
    seen: dict[str, str] = {}

    async def _drive(name: str) -> None:
        runtime.configure(_runtime(name))
        await asyncio.sleep(0)  # yield control, exactly like a real await-ing task
        seen[name] = runtime.current().kopicode_binary

    await asyncio.gather(_drive("project-a"), _drive("project-b"))

    assert seen == {"project-a": "project-a", "project-b": "project-b"}


async def test_a_task_created_after_configure_inherits_it() -> None:
    """`asyncio.create_task` copies the calling context -- a nested task spawned
    *after* `configure()` (exactly `cuttlefish.fleet`'s own launch order) sees the
    same `Runtime` its parent just set, without a second `configure()` call."""
    runtime.configure(_runtime("parent-binary"))

    async def _nested() -> str:
        return runtime.current().kopicode_binary

    result = await asyncio.create_task(_nested())
    assert result == "parent-binary"
