"""Unit: KopicodeBackend's own policy-file lifecycle (moved from
tests/unit/tasks/test_delegate.py once that lifecycle became backend-specific,
ADR-0005).

Uses a nonexistent binary name so this runs with no real kopicode needed --
DelegationError fires fast (binary not found), but only *after* the policy
file was written and passed, exercising the write-then-cleanup cycle either
way.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import cuttlefish.agents.kopicode
from cuttlefish.agents.kopicode import KopicodeBackend
from cuttlefish.agents.outcome import DelegationError


async def test_the_temporary_policy_file_is_cleaned_up_after_the_call(tmp_path: Path) -> None:
    backend = KopicodeBackend("kopicode-binary-that-does-not-exist", transport="print")
    tmp_dir = Path(tempfile.gettempdir())
    files_before = set(tmp_dir.glob("cuttlefish-policy-*"))

    with pytest.raises(DelegationError):
        await backend.delegate(
            task_text="add a .gitignore entry",
            root=str(tmp_path),
            allow=None,
            secrets={},
            sandbox_provider=None,
        )

    files_after = set(tmp_dir.glob("cuttlefish-policy-*"))
    assert files_after == files_before


async def test_a_declared_allowlist_reaches_the_written_policy_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KAN-1011: `allow` is forwarded to `write_policy_file`, not silently dropped."""
    backend = KopicodeBackend("kopicode-binary-that-does-not-exist", transport="print")
    captured: dict[str, object] = {}
    original_write_policy_file = cuttlefish.agents.kopicode.write_policy_file

    def spy(path: Path, *, root: str, allow: list[list[str]] | None = None) -> None:
        captured["allow"] = allow
        original_write_policy_file(path, root=root, allow=allow)

    monkeypatch.setattr(cuttlefish.agents.kopicode, "write_policy_file", spy)

    declared = [["go", "test"], ["npm", "test"]]
    with pytest.raises(DelegationError):
        await backend.delegate(
            task_text="run the tests",
            root=str(tmp_path),
            allow=declared,
            secrets={},
            sandbox_provider=None,
        )

    assert captured["allow"] == declared


class _ExecOnlyProvider:
    """A sandbox provider that cannot stream (like E2B today): no ``spawn``."""

    BACKEND_NAME = "exec-only"


async def test_a_sandbox_that_cannot_stream_keeps_the_run_print_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def must_not_serve(**_: object) -> None:
        raise AssertionError("serve must not be used without a streaming provider")

    monkeypatch.setattr(cuttlefish.agents.kopicode, "run_kopicode_serve", must_not_serve)
    backend = KopicodeBackend("kopicode-binary-that-does-not-exist")

    with pytest.raises(DelegationError, match="not found"):
        await backend.delegate(
            task_text="x",
            root=str(tmp_path),
            allow=None,
            secrets={},
            sandbox_provider=_ExecOnlyProvider(),  # type: ignore[arg-type]
        )


async def test_auto_mode_needs_the_serve_transport(tmp_path: Path) -> None:
    backend = KopicodeBackend("kopicode-binary-that-does-not-exist", transport="print")
    with pytest.raises(DelegationError, match="serve transport"):
        await backend.delegate(
            task_text="t",
            root=str(tmp_path),
            allow=[],
            secrets={},
            sandbox_provider=None,
            mode="auto",
        )
