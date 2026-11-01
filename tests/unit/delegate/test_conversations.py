"""Unit: the per-role conversation store and its switch (CUT-92, CUT-93)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cuttlefish.delegate import conversations


def test_remember_recall_and_forget_are_per_backend_and_key(tmp_path: Path) -> None:
    root = str(tmp_path)
    conversations.remember(root, "codex", "dev", "t1")
    conversations.remember(root, "claude-code", "dev", "s1")
    assert conversations.recall(root, "codex", "dev") == "t1"
    assert conversations.recall(root, "claude-code", "dev") == "s1"
    assert conversations.recall(root, "codex", "other") is None
    conversations.forget(root, "codex", "dev")
    assert conversations.recall(root, "codex", "dev") is None
    assert conversations.recall(root, "claude-code", "dev") == "s1"
    conversations.forget(root, "codex", "never-there")


def test_forget_all_drops_a_key_for_every_backend(tmp_path: Path) -> None:
    root = str(tmp_path)
    conversations.remember(root, "codex", "dev", "t1")
    conversations.remember(root, "claude-code", "dev", "s1")
    conversations.remember(root, "claude-code", "rev", "s2")
    conversations.forget_all(root, "dev")
    assert conversations.recall(root, "codex", "dev") is None
    assert conversations.recall(root, "claude-code", "dev") is None
    assert conversations.recall(root, "claude-code", "rev") == "s2"


def test_a_damaged_file_reads_as_empty(tmp_path: Path) -> None:
    (tmp_path / ".cuttlefish").mkdir()
    (tmp_path / ".cuttlefish" / "conversations.json").write_text("{not json")
    assert conversations.recall(str(tmp_path), "codex", "dev") is None
    conversations.remember(str(tmp_path), "codex", "dev", "t1")
    assert conversations.recall(str(tmp_path), "codex", "dev") == "t1"


@pytest.mark.parametrize(
    ("env", "backend", "expected"),
    [
        ({"CUTTLEFISH_RESUME": "1"}, "codex", True),
        ({"CUTTLEFISH_RESUME": "1"}, "claude-code", True),
        ({"CUTTLEFISH_CODEX_RESUME": "1"}, "codex", True),
        ({"CUTTLEFISH_CODEX_RESUME": "1"}, "claude-code", False),
        ({}, "codex", False),
        ({"CUTTLEFISH_RESUME": "0"}, "codex", False),
    ],
)
def test_the_switch_and_its_legacy_name(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], backend: str, expected: bool
) -> None:
    for name in ("CUTTLEFISH_RESUME", "CUTTLEFISH_CODEX_RESUME"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert conversations.resume_enabled(backend) is expected


def test_a_team_role_worktree_shares_its_projects_registry(tmp_path: Path) -> None:
    tree = tmp_path / ".cuttlefish" / "worktrees" / "team-t1-dev"
    tree.mkdir(parents=True)
    conversations.remember(str(tree), "kopicode", "dev", "ses-1")
    assert conversations.recall(str(tmp_path), "kopicode", "dev") == "ses-1"
    assert not (tree / ".cuttlefish").exists()
