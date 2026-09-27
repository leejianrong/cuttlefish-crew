"""Integration: a real edit landing through the real `codex` binary, live.

The Codex counterpart to `test_claude_code_real.py`'s own live proof. Gated
behind ``requires_codex_live`` rather than a credential-presence check: this
build's own Codex authenticates via an OAuth session with no
``OPENAI_API_KEY`` set at all (verified live, 2026-09-28 -- see
``cuttlefish.delegate.codex``'s own module doc comment), so presence of a
usable credential can't be inferred from the environment. Only runs when an
operator deliberately opts in (``CUTTLEFISH_TEST_CODEX_LIVE=1``), the same
cost-bearing discipline ``requires_claude_code_live``/``requires_e2b_credential``
already take.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cuttlefish.delegate.codex import run_codex


def _init_scratch_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "cuttlefish-tests"], cwd=root, check=True)
    (root / "README.md").write_text("a scratch checkout for a live Codex test\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.mark.requires_codex_live
async def test_a_real_write_lands_and_is_classified_as_completed(tmp_path: Path) -> None:
    root = tmp_path / "scratch"
    root.mkdir()
    _init_scratch_repo(root)

    outcome = await run_codex(
        binary="codex",
        task_text=(
            "Create a file named LIVE_TEST.txt containing the single line: "
            "cuttlefish live test. Then stop."
        ),
        root=str(root),
        allow=[["true"]],
        timeout=120,
    )

    assert outcome.kind == "completed"
    assert outcome.edited_paths == ["LIVE_TEST.txt"]
    assert (root / "LIVE_TEST.txt").read_text().strip() == "cuttlefish live test"
    assert outcome.tokens is not None and outcome.tokens > 0
    assert outcome.cost_usd is None  # Codex reports no dollar figure at all


@pytest.mark.requires_codex_live
async def test_no_declared_allowlist_uses_read_only_sandbox_and_refuses_the_edit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "scratch"
    root.mkdir()
    _init_scratch_repo(root)

    outcome = await run_codex(
        binary="codex",
        task_text="Create a file named should-not-exist.txt containing the word nope.",
        root=str(root),
        timeout=120,
    )

    assert outcome.kind == "refused"
    assert not (root / "should-not-exist.txt").exists()
