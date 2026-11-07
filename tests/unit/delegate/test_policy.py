from __future__ import annotations

from pathlib import Path

from cuttlefish.delegate.policy import write_policy_file


def test_default_allowlist_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "policy.toml"
    write_policy_file(path, root="/abs/scratch")

    content = path.read_text()
    assert content == 'root = "/abs/scratch"\nallow = []\n'


def test_a_declared_allowlist_is_rendered_as_allow_commands(tmp_path: Path) -> None:
    path = tmp_path / "policy.toml"
    write_policy_file(path, root="/abs/scratch", allow=[["go", "test"], ["git", "status"]])

    assert path.read_text() == (
        'root = "/abs/scratch"\nallow = []\nallow_commands = [["go", "test"], ["git", "status"]]\n'
    )


def test_entries_kopicode_would_reject_the_file_for_are_dropped(tmp_path: Path) -> None:
    path = tmp_path / "policy.toml"
    allow = [["find"], ["npx", "tsc"], ["sh"], ["/usr/bin/sudo", "x"], ["cd"], ["ls"]]
    write_policy_file(path, root="/abs/scratch", allow=allow)

    assert path.read_text() == 'root = "/abs/scratch"\nallow = []\nallow_commands = [["ls"]]\n'
