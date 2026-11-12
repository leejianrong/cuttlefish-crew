"""CUT-215: a worker writes the sync set down, checks it, and never clobbers its owner's skills."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

from cuttlefish.worker.sync import MARKER, Synced, place_instructions


def _file(path: str, text: str = "x", sha: str | None = None) -> dict[str, Any]:
    data = text.encode()
    return {
        "path": path,
        "sha256": sha or hashlib.sha256(data).hexdigest(),
        "content": base64.b64encode(data).decode(),
    }


def _rig(tmp_path: Path) -> tuple[Synced, Path, Path]:
    folder, skills = tmp_path / "synced", tmp_path / "claude" / "skills"
    return Synced(folder, skills), folder, skills


def test_files_are_written_reported_and_a_skill_is_installed_for_claude(tmp_path: Path) -> None:
    synced, folder, skills = _rig(tmp_path)
    assert synced.have() == {}
    held = synced.apply(
        [_file("skills/review/SKILL.md", "# R"), _file("skills/review/refs/a.md", "a"),
         _file("instructions/team.md", "t"), _file("mcp.json", "{}")], [],
    )  # fmt: skip
    assert set(held) == {
        "skills/review/SKILL.md",
        "skills/review/refs/a.md",
        "instructions/team.md",
        "mcp.json",
    }
    assert synced.have() == held
    assert (folder / "skills/review/SKILL.md").read_text() == "# R"
    assert (skills / "review/SKILL.md").read_text() == "# R"
    assert (skills / "review/refs/a.md").read_text() == "a" and (
        skills / "review" / MARKER
    ).exists()
    assert not (skills / "instructions").exists() and not (skills / "mcp.json").exists()


def test_a_path_or_digest_the_controller_would_not_send_is_not_written(tmp_path: Path) -> None:
    synced, folder, skills = _rig(tmp_path)
    held = synced.apply(
        [
            _file("../escape.md", "x"),
            _file("skills/../../escape.md", "x"),
            _file("/abs.md", "x"),
            _file("skills/a/SKILL.md", "x", sha="0" * 64),  # does not match its content
            {"path": "skills/b/SKILL.md", "sha256": "x", "content": "!!notbase64"},
            _file("skills/c/SKILL.md", "fine"),
        ],
        [],
    )
    assert list(held) == ["skills/c/SKILL.md"]
    assert not (tmp_path / "escape.md").exists() and not (folder.parent / "escape.md").exists()
    assert not (skills / "a").exists() and not (skills / "b").exists()


def test_a_skill_the_machine_owner_made_is_never_overwritten_or_removed(tmp_path: Path) -> None:
    synced, folder, skills = _rig(tmp_path)
    (skills / "review").mkdir(parents=True)
    (skills / "review" / "SKILL.md").write_text("mine")
    synced.apply([_file("skills/review/SKILL.md", "theirs")], [])
    assert (
        skills / "review" / "SKILL.md"
    ).read_text() == "mine"  # theirs stays in the synced folder only
    assert (folder / "skills/review/SKILL.md").read_text() == "theirs"
    synced.apply([], ["skills/review/SKILL.md"])
    assert (skills / "review" / "SKILL.md").read_text() == "mine"
    assert not (folder / "skills/review/SKILL.md").exists()


def test_a_changed_file_is_replaced_and_a_dropped_one_goes_everywhere(tmp_path: Path) -> None:
    synced, folder, skills = _rig(tmp_path)
    synced.apply([_file("skills/s/SKILL.md", "one"), _file("skills/s/b.md", "b")], [])
    synced.apply([_file("skills/s/SKILL.md", "two")], ["skills/s/b.md"])
    assert (skills / "s/SKILL.md").read_text() == "two" and not (skills / "s/b.md").exists()
    held = synced.apply([], ["skills/s/SKILL.md", "../nope"])
    assert held == {} and not (skills / "s").exists() and not (folder / "skills").exists()


def test_instruction_files_are_put_in_a_checkout_and_dropped_ones_removed(tmp_path: Path) -> None:
    synced, folder, _skills = _rig(tmp_path)
    checkout = tmp_path / "work" / "p1"
    place_instructions(folder, checkout)  # nothing synced yet: nothing made
    assert not (checkout / ".cuttlefish").exists()
    synced.apply([_file("instructions/team.md", "t"), _file("instructions/old.md", "o")], [])
    place_instructions(folder, checkout)
    assert (checkout / ".cuttlefish/instructions/team.md").read_text() == "t"
    synced.apply([], ["instructions/old.md"])
    place_instructions(folder, checkout)
    assert not (checkout / ".cuttlefish/instructions/old.md").exists()
    assert (checkout / ".cuttlefish/instructions/team.md").exists()
