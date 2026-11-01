"""The present instruction file and its examples (CUT-194, ADR-0056)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cuttlefish import present
from cuttlefish.artifacts import AGENT_NOTE, list_artifacts

KINDS = ("doc", "slides", "wireframe", "animation", "demo", "dashboard")


def test_install_writes_skill_and_one_example_per_kind(tmp_path: Path) -> None:
    assert present.install(tmp_path) is True
    assert (tmp_path / present.SKILL_PATH).is_file()
    for kind in KINDS:
        assert list((tmp_path / present.PRESENT_DIR / "examples" / kind).glob("example.*"))


def test_install_is_idempotent_and_repairs_edits(tmp_path: Path) -> None:
    present.install(tmp_path)
    assert present.install(tmp_path) is False
    skill = tmp_path / present.SKILL_PATH
    skill.write_text("changed")
    assert present.install(tmp_path) is True
    assert "Present your work" in skill.read_text()


def test_missing_root_is_skipped(tmp_path: Path) -> None:
    assert present.install(tmp_path / "nope") is False


def test_note_points_at_the_skill() -> None:
    assert present.SKILL_PATH in AGENT_NOTE


@pytest.mark.parametrize("kind", KINDS)
def test_examples_are_self_contained(tmp_path: Path, kind: str) -> None:
    present.install(tmp_path)
    (example,) = (tmp_path / present.PRESENT_DIR / "examples" / kind).glob("example.*")
    text = example.read_text()
    if example.suffix == ".html":
        assert f'content="{kind}"' in text and "<title>" in text
        assert not re.search(r"<script[^>]+src=|<link\b|https?://|@import|fetch\(", text)


def test_examples_are_viewable_artifacts(tmp_path: Path) -> None:
    (tmp_path / "p").mkdir()
    present.install(tmp_path / "p")
    folder = tmp_path / "p" / ".cuttlefish" / "artifacts"
    for kind in KINDS:
        (src,) = (tmp_path / "p" / present.PRESENT_DIR / "examples" / kind).glob("example.*")
        (folder / kind).mkdir(parents=True, exist_ok=True)
        (folder / kind / src.name).write_bytes(src.read_bytes())
    kinds = {a.path.split("/")[0]: a.kind for a in list_artifacts(str(tmp_path / "p"))}
    assert kinds["slides"] == "html" and kinds["doc"] == "markdown"
