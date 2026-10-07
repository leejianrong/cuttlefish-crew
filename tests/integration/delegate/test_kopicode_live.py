"""Integration: a real edit landing through the real kopicode binary, live.

This is what tests/integration/delegate/test_kopicode_real.py's own docstring
forward-references: a scenario that needs kopicode to actually reach a model,
gated behind a real credential rather than mocked or skipped. It costs a real API
call, so it's kept to exactly what the rest of this suite cannot cover any other
way: classify_stream's own unit tests only assert against a synthetic, and
therefore not necessarily accurate, event shape. This is the regression test for
the gap that let past that — KAN-1008's live run found that write_file/delete_file
never emit edit_applied, so a real file write was silently classified as "no edit
needed" until that was fixed.

No live denial scenario here: prompting a real model to reliably attempt an
out-of-policy action (rather than simply doing the task it was asked) isn't a
thing this suite can promise deterministically, and the deny path itself is
already covered by classify_stream's unit tests plus test_policy_real.py's
real-parser round trip.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cuttlefish.delegate.kopicode import run_kopicode
from cuttlefish.delegate.policy import write_policy_file


def _init_scratch_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "cuttlefish-tests"], cwd=root, check=True)
    (root / "README.md").write_text("a scratch checkout for a live kopicode test\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.mark.requires_kopicode
@pytest.mark.requires_live_credential
async def test_a_real_write_lands_and_is_classified_as_completed(tmp_path: Path) -> None:
    root = tmp_path / "scratch"
    root.mkdir()
    _init_scratch_repo(root)

    policy_path = tmp_path / "policy.toml"
    write_policy_file(policy_path, root=str(root))

    outcome = await run_kopicode(
        binary="kopicode",
        task_text=(
            "Create a file named LIVE_TEST.txt containing the single line: cuttlefish live test"
        ),
        root=str(root),
        policy_file=str(policy_path),
        # The credential is passed the way a real delegation passes it: declared, not inherited
        # (an agent's environment is an allowlist, V5-E4).
        env={"OPENROUTER_API_KEY": os.environ["OPENROUTER_API_KEY"]},
        timeout=120,
    )

    assert outcome.kind == "completed"
    assert outcome.edited_paths == ["LIVE_TEST.txt"]
    assert (root / "LIVE_TEST.txt").read_text().strip() == "cuttlefish live test"
    # KAN-1714/ADR-0019: a real write is also recorded as a real tool call.
    assert any(call.status == "ok" for call in outcome.tool_calls)
