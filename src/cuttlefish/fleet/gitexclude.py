"""Keep cuttlefish's own folders out of a project's ``git status`` (CUT-159).

A team writes ``.cuttlefish/`` (the journal, artifacts) and ``.satay/`` (the workflow store) into
the project's root. Left alone they show up as untracked files, and an agent that runs ``git add .``
or lists them in a commit puts the daemon's state in the person's repository (seen twice in dogfood
runs, 2026-10-19). They go in ``.git/info/exclude``, the repository-local ignore file: it changes
nothing that is committed or shared, and a project that already ignores them is left as it is.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Sequence
from pathlib import Path

_LOG = logging.getLogger(__name__)

ENTRIES: tuple[str, ...] = (".cuttlefish/", ".satay/")
_NOTE = "# cuttlefish-crew: its own state, not part of the project"


def ensure_excluded(root: str | Path, entries: Sequence[str] = ENTRIES) -> bool:
    """Add what is missing of ``entries`` to the repo's ``info/exclude``; ``True`` if it changed.

    A plain ``.git`` directory is read directly. Where ``.git`` is a file (a linked worktree or a
    submodule) ``git rev-parse --git-path`` names the exclude file; a folder that is not a
    repository has none. Best effort: a read-only or odd ``.git``, or no ``git``, is logged and
    skipped, never a reason to refuse a team.
    """
    git = Path(root) / ".git"
    if git.is_dir():
        path = git / "info" / "exclude"
    elif git.is_file():
        found = _linked_exclude(Path(root))
        if found is None:
            return False
        path = found
    else:
        return False
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        have = {line.strip().rstrip("/") for line in existing.splitlines()}
        missing = [entry for entry in entries if entry.rstrip("/") not in have]
        if not missing:
            return False
        path.parent.mkdir(exist_ok=True)
        lead = "" if not existing or existing.endswith("\n") else "\n"
        path.write_text(f"{existing}{lead}{_NOTE}\n" + "\n".join(missing) + "\n", encoding="utf-8")
    except OSError as exc:
        _LOG.info("could not update %s: %s", path, exc)
        return False
    return True


def _linked_exclude(root: Path) -> Path | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-path", "info/exclude"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        _LOG.info("could not find the git dir of %s: %s", root, exc)
        return None
    return root / out if out else None
