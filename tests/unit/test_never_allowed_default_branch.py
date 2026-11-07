"""CUT-183: what an unattended (Auto) goal may not do to the default branch."""

from __future__ import annotations

import pytest

from cuttlefish.delegate.never_allowed import default_branch_reason


@pytest.mark.parametrize(
    ("line", "current", "refused"),
    [
        ("git commit -m x", "main", True),
        ("git add -A && git commit -am x", "main", True),
        ("git -C . merge feature", "master", True),
        ("git commit -m x", "cuttlefish/goal", False),
        ("git status", "main", False),
        ("git switch main", "cuttlefish/goal", True),
        ("git checkout -b topic", "cuttlefish/goal", False),
        ("echo git commit", "main", False),
    ],
)
def test_default_branch_reason(line: str, current: str, refused: bool) -> None:
    assert (default_branch_reason(line, current) is not None) is refused
