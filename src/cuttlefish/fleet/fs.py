"""The folder picker's server side (docs/SLICES.md V4-E, ADR-0026).

The daemon runs on the operator's own machine, so it can show the folders a project could be
registered against. That is a filesystem browser behind an HTTP port, so it is deliberately
narrow:

- It lists **directories only**, never a file's name or contents.
- It answers only inside the configured **browse roots** (default: the home directory,
  ``cuttlefish serve --browse-root``). A path is resolved first (``..`` and symlinks followed),
  and anything that resolves outside every root is refused, not trimmed.
- A **symlink is shown only if its target is also inside a root**, so a link out of the tree
  is invisible rather than a way through it.
- **Hidden** entries (a leading dot) are not listed. A project folder is not hidden, and a
  ``.git`` listing would only be noise.

``inspect`` is the one place git is run (``rev-parse``, ``status``, ``log``), read-only, with a
short timeout and ``GIT_OPTIONAL_LOCKS=0`` so it never touches the index a running agent is
using. Both calls are blocking and meant to run under ``asyncio.to_thread``.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

#: A listing past this many folders is truncated (and says so) rather than sent whole.
MAX_ENTRIES = 500

_GIT_TIMEOUT_SECONDS = 3.0

#: Marker file -> what it says about the project. First match per label wins; order is display
#: order. Deliberately a handful of markers, not a language detector.
_MARKERS: tuple[tuple[str, str], ...] = (
    ("pyproject.toml", "Python"),
    ("requirements.txt", "Python"),
    ("package.json", "JavaScript"),
    ("tsconfig.json", "TypeScript"),
    ("go.mod", "Go"),
    ("Cargo.toml", "Rust"),
    ("Gemfile", "Ruby"),
    ("pom.xml", "Java"),
    ("build.gradle", "Java"),
)


class OutsideBrowseRootsError(PermissionError):
    """The requested path resolves outside every browse root."""


class NotAFolderError(NotADirectoryError):
    """The requested path is not a folder (or does not exist)."""


@dataclass(frozen=True, slots=True)
class Folder:
    name: str
    path: str
    is_git: bool


@dataclass(frozen=True, slots=True)
class Listing:
    path: str
    root: str
    parent: str | None
    folders: tuple[Folder, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class Inspection:
    path: str
    name: str
    is_git: bool
    branch: str | None
    dirty: bool | None
    last_commit: str | None
    languages: tuple[str, ...]


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


class FolderBrowser:
    """Lists and inspects folders under a fixed set of roots."""

    def __init__(self, roots: Sequence[Path]) -> None:
        resolved = [Path(root).expanduser().resolve() for root in roots]
        if not resolved:
            raise ValueError("a FolderBrowser needs at least one browse root")
        self._roots = tuple(dict.fromkeys(resolved))

    @property
    def roots(self) -> tuple[Path, ...]:
        return self._roots

    def _contain(self, requested: str | None) -> tuple[Path, Path]:
        """`(resolved path, the root that holds it)`, or raise."""
        path = self._roots[0] if not requested else Path(requested).expanduser()
        try:
            resolved = path.resolve(strict=True)
        except (FileNotFoundError, NotADirectoryError):
            raise NotAFolderError(f"{requested!r} is not a folder") from None
        except OSError as exc:  # a symlink loop, a permission error on a parent
            raise NotAFolderError(f"{requested!r} cannot be read: {exc.strerror}") from None
        for root in self._roots:
            if _inside(resolved, root):
                if not resolved.is_dir():
                    raise NotAFolderError(f"{requested!r} is not a folder")
                if any(part.startswith(".") for part in resolved.relative_to(root).parts):
                    raise OutsideBrowseRootsError(f"{requested!r} is a hidden folder")
                return resolved, root
        raise OutsideBrowseRootsError(f"{requested!r} is outside the folders this daemon browses")

    def list_folders(self, requested: str | None = None) -> Listing:
        path, root = self._contain(requested)
        folders: list[Folder] = []
        truncated = False
        try:
            entries = sorted(os.scandir(path), key=lambda entry: entry.name.lower())
        except PermissionError:
            entries = []
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if not entry.is_dir():  # follows a symlink: a link to a file is not a folder
                    continue
                target = Path(entry.path).resolve(strict=True)
            except OSError:
                continue
            if not _inside(target, root):
                continue
            if len(folders) >= MAX_ENTRIES:
                truncated = True
                break
            folders.append(
                Folder(name=entry.name, path=str(target), is_git=(target / ".git").exists())
            )
        parent = None if path == root else str(path.parent)
        return Listing(
            path=str(path),
            root=str(root),
            parent=parent,
            folders=tuple(folders),
            truncated=truncated,
        )

    def inspect(self, requested: str) -> Inspection:
        path, _root = self._contain(requested)
        is_git = (path / ".git").exists()
        branch: str | None = None
        dirty: bool | None = None
        last_commit: str | None = None
        if is_git:
            # `--show-current` names the branch even before the first commit; it is empty on a
            # detached HEAD, where the short commit is the honest answer.
            branch = _git(path, "branch", "--show-current") or _git(
                path, "rev-parse", "--short", "HEAD"
            )
            status = _git(path, "status", "--porcelain")
            dirty = None if status is None else bool(status)
            last_commit = _git(path, "log", "-1", "--format=%cr")
        languages = tuple(
            dict.fromkeys(label for marker, label in _MARKERS if (path / marker).exists())
        )
        return Inspection(
            path=str(path),
            name=path.name,
            is_git=is_git,
            branch=branch,
            dirty=dirty,
            last_commit=last_commit,
            languages=languages,
        )


def _git(cwd: Path, *args: str) -> str | None:
    """One read-only git call's stdout (stripped), or ``None`` if git is missing, slow or fails."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()
