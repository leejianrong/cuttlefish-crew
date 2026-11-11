"""Share ignored directories into, and copy listed files into, a goal's worktree (CUT-296).

A goal's worktree (ADR-0048) starts clean, so a project that needs ``node_modules`` or a local
config file there either reinstalls or goes without. A project may list, in its own settings (the
registry, Admin tier: not a file in the repository, which an agent could edit):

- **directories to link**: a symlink to the checkout's copy, for something big that is only read
  (``node_modules``, a build cache). A symlink, not a copy: a copy of ``node_modules`` is slow
  and large. The cost is that the directory is *shared*: an install or a build run in the
  worktree changes the checkout's too, so it is for dependencies, not for anything a goal edits.
- **files to copy**: a real copy, so an edit in the worktree never reaches the original and a
  copy can never be written through a link. For small local files (``config/local.json``).

The safest default is nothing: both lists are empty until a person sets them. Rules that hold
whatever the lists say (checked when they are set and again when they are applied, because the
checkout can change in between):

- the path is relative, has no ``..``, no glob characters, and resolves inside the project root;
- it is not ``.git``, ``.cuttlefish`` or ``.satay``, and its name is not a secret by the one list
  ``fleet.goaldiff.looks_secret`` already uses (``.env``, ``*.pem``, ``*.key``, ssh keys,
  ``.npmrc`` ...), so a secret is never copied to a new place where it could be committed or
  read by a tool that was never given it (ADR-0006). Project secrets stay in the secrets store;
- git must **ignore** it, so what is shared can never show up as a change a ``git add -A``
  would commit (a linked directory is added to the repository's local exclude, because a
  ``node_modules/`` rule does not match a symlink);
- nothing is overwritten: a path already in the worktree is left alone;
- a file is a regular file of at most 5 MB, not a link.

A path that cannot be shared is skipped and named in the report with why; it never stops a goal.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from cuttlefish.fleet import gitexclude
from cuttlefish.fleet.goaldiff import looks_secret

_LOG = logging.getLogger(__name__)

MAX_ENTRIES = 20
MAX_ENTRY_CHARS = 200
MAX_COPY_BYTES = 5_000_000
_RESERVED_FIRST = frozenset({".git", ".cuttlefish", ".satay", ".ssh", ".aws", ".gnupg"})
_GLOB = frozenset("*?[]{}!")


class ShareError(ValueError):
    """A setting that may not be saved, or a worktree that is not where it must be."""


@dataclasses.dataclass
class Report:
    linked: list[str] = dataclasses.field(default_factory=list)
    copied: list[str] = dataclasses.field(default_factory=list)
    skipped: list[tuple[str, str]] = dataclasses.field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        return {
            "linked": self.linked,
            "copied": self.copied,
            "skipped": [{"path": p, "why": why} for p, why in self.skipped],
        }


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _normalise(entry: object) -> str:
    """One relative path, tidied, or :class:`ShareError` saying what is wrong with it."""
    if not isinstance(entry, str) or not entry.strip():
        raise ShareError("a path must be text and not empty")
    text = entry.strip()
    if "\x00" in text or len(text) > MAX_ENTRY_CHARS:
        raise ShareError(
            f"{text[:40]!r} is not a usable path (at most {MAX_ENTRY_CHARS} characters)"
        )
    if _GLOB.intersection(text):
        raise ShareError(f"{text!r} names a pattern; list one path at a time")
    path = PurePosixPath(text)
    if path.is_absolute() or text.startswith("~"):
        raise ShareError(f"{text!r} must be relative to the project folder")
    parts = [p for p in path.parts if p != "."]
    if ".." in parts:
        raise ShareError(f"{text!r} reaches outside the project folder")
    if not parts:
        raise ShareError("the project folder itself cannot be shared; name something inside it")
    if parts[0] in _RESERVED_FIRST:
        raise ShareError(f"{parts[0]!r} is reserved and cannot be shared")
    clean = "/".join(parts)
    if looks_secret(clean):
        raise ShareError(f"{clean!r} looks like a secret; secrets are never copied or linked")
    return clean


def _validate(root: str, entries: Sequence[str]) -> tuple[str, ...]:
    if isinstance(entries, str) or not isinstance(entries, Sequence):
        raise ShareError("expected a list of paths")
    if len(entries) > MAX_ENTRIES:
        raise ShareError(f"at most {MAX_ENTRIES} paths")
    seen: dict[str, None] = {}
    base = Path(root).resolve()
    for entry in entries:
        clean = _normalise(entry)
        target = base / clean
        if os.path.lexists(target) and not _inside(target.resolve(), base):
            raise ShareError(f"{clean!r} resolves outside the project folder")
        seen.setdefault(clean)
    return tuple(seen)


def validate_dirs(root: str, entries: Sequence[str]) -> tuple[str, ...]:
    """The directories to link, normalised and without repeats, or :class:`ShareError`."""
    return _validate(root, entries)


def validate_files(root: str, entries: Sequence[str]) -> tuple[str, ...]:
    """The files to copy, normalised and without repeats, or :class:`ShareError`."""
    return _validate(root, entries)


def _ignored(root: Path, rel: str) -> bool:
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", "--", rel],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _refusal(base: Path, rel: str, *, directory: bool) -> str | None:
    """Why `rel` may not be shared from the checkout at `base`, or ``None``."""
    if looks_secret(rel.removeprefix("./")):
        return "it looks like a secret"
    try:
        clean = _normalise(rel)
    except ShareError:
        return "it is not a path that can be shared"
    src = base / clean
    if not os.path.lexists(src):
        return "it is not in the project"
    if src.is_symlink() or not _inside(src.resolve(), base):
        return "it is a link or leads outside the project"
    if directory and not src.is_dir():
        return "it is not a directory"
    if not directory:
        if not src.is_file():
            return "it is not a regular file"
        if src.stat().st_size > MAX_COPY_BYTES:
            return f"it is larger than {MAX_COPY_BYTES // 1_000_000} MB"
    if not _ignored(base, clean):
        return "git does not ignore it, so it could land in a commit"
    return None


def apply(root: str, worktree: str, dirs: Sequence[str], files: Sequence[str]) -> Report:
    """Link `dirs` and copy `files` from the checkout at `root` into the worktree at `worktree`.

    Never raises for a path it cannot share (it is skipped, with why); raises
    :class:`ShareError` only when the worktree is not inside the project, which no setting can
    make right."""
    base = Path(root).resolve()
    tree = Path(worktree).resolve()
    if not _inside(tree, base):
        raise ShareError("the worktree must be inside the project folder")
    report = Report()
    linked_ignores: list[str] = []
    for directory, entries in ((True, dirs), (False, files)):
        for rel in entries:
            destination = tree / rel
            why = _refusal(base, rel, directory=directory)
            if why is None and os.path.lexists(destination):
                why = "it is already there"
            if why is None and not _inside(destination.parent.resolve(), tree):
                why = "its folder leads outside the worktree"
            if why is not None:
                report.skipped.append((rel, why))
                continue
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if directory:
                    destination.symlink_to(base / rel)
                    linked_ignores.append(f"/{rel}")
                    report.linked.append(rel)
                else:
                    shutil.copy2(base / rel, destination, follow_symlinks=False)
                    report.copied.append(rel)
            except OSError as exc:
                report.skipped.append((rel, f"it could not be shared ({exc.strerror or exc})"))
    if linked_ignores:
        # `node_modules/` does not match a symlink, so a linked directory would be an untracked
        # change: keep it out of status the way cuttlefish's own folders are kept out.
        gitexclude.ensure_excluded(base, linked_ignores)
    for path, why in report.skipped:
        _LOG.info("worktree share: %s not shared: %s", path, why)
    return report
