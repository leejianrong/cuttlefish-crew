"""The artifacts folder: what is listed, what is served, and what is refused (CUT-97)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cuttlefish.artifacts import (
    ARTIFACTS_DIR,
    ArtifactNotFoundError,
    ArtifactNotPreviewableError,
    ArtifactTooLargeError,
    list_artifacts,
    read_artifact,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / ARTIFACTS_DIR).mkdir(parents=True)
    return tmp_path


def _write(root: Path, name: str, data: bytes | str = "x") -> Path:
    path = root / ARTIFACTS_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def test_a_missing_folder_is_an_empty_list(tmp_path: Path) -> None:
    assert list_artifacts(str(tmp_path)) == []


def test_files_are_listed_newest_first_with_their_kind(root: Path) -> None:
    old = _write(root, "old.md", "# old")
    new = _write(root, "sub/new.png", b"\x89PNG")
    other = _write(root, "data.csv", "a,b")
    os.utime(other, (500, 500))
    os.utime(old, (1000, 1000))
    os.utime(new, (3000, 3000))
    listed = list_artifacts(str(root))
    assert [(a.path, a.kind) for a in listed][:2] == [
        ("sub/new.png", "image"),
        ("old.md", "markdown"),
    ]
    assert {a.path: a.kind for a in listed}["data.csv"] == "other"


def test_hidden_files_and_folders_are_not_listed(root: Path) -> None:
    _write(root, ".secret.md")
    _write(root, ".git/config.md")
    assert list_artifacts(str(root)) == []


def test_markdown_and_images_are_served_with_their_type(root: Path) -> None:
    _write(root, "a.md", "# hi")
    _write(root, "p.svg", "<svg/>")
    assert read_artifact(str(root), "a.md") == (b"# hi", "text/markdown; charset=utf-8")
    assert read_artifact(str(root), "p.svg")[1] == "image/svg+xml"


@pytest.mark.parametrize("name", ["../secret.md", "/etc/passwd", "a/../../x.md", "", "a\0.md"])
def test_a_path_outside_the_folder_is_refused(root: Path, name: str) -> None:
    (root / "secret.md").write_text("no")
    with pytest.raises(ArtifactNotFoundError):
        read_artifact(str(root), name)


def test_a_symlink_out_of_the_folder_is_neither_listed_nor_served(
    root: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("secret")
    (root / ARTIFACTS_DIR / "link.md").symlink_to(outside)
    (root / ARTIFACTS_DIR / "dir").symlink_to(tmp_path, target_is_directory=True)
    assert list_artifacts(str(root)) == []
    with pytest.raises(ArtifactNotFoundError):
        read_artifact(str(root), "link.md")
    with pytest.raises(ArtifactNotFoundError):
        read_artifact(str(root), "dir/outside.md")


def test_a_symlink_inside_the_folder_is_fine(root: Path) -> None:
    target = _write(root, "real.md", "ok")
    (root / ARTIFACTS_DIR / "alias.md").symlink_to(target)
    assert read_artifact(str(root), "alias.md")[0] == b"ok"


def test_other_files_are_listed_but_not_served(root: Path) -> None:
    _write(root, "page.html", "<script>alert(1)</script>")
    with pytest.raises(ArtifactNotPreviewableError):
        read_artifact(str(root), "page.html")


def test_a_file_over_its_limit_is_refused(root: Path) -> None:
    _write(root, "big.md", b"x" * 1_000_001)
    with pytest.raises(ArtifactTooLargeError):
        read_artifact(str(root), "big.md")
