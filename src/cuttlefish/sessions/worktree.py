"""A git worktree and branch per goal (CUT-238, ADR-0048).

The worktree lives under ``<root>/.cuttlefish/worktrees/``, inside the project root, so everything
that holds an agent to the project folder (the never-allowed list, the hook's edit check) still
holds there, and ``.cuttlefish/`` is already out of ``git status``. It is made from the checkout's
current ``HEAD`` on a new ``cuttlefish/<slug>`` branch; the checkout and its branch are not touched.
A finished goal's worktree is kept, because it holds the work: remove it with
``git worktree remove``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path


class WorktreeError(RuntimeError):
    pass


def _git(root: str | Path, *args: str) -> str:
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=60, check=True
        )
    except subprocess.CalledProcessError as exc:
        raise WorktreeError((exc.stderr or exc.stdout).strip() or f"git {args[0]} failed") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorktreeError(f"git {args[0]} failed: {exc}") from exc
    return done.stdout


def slug(goal: str, *, now: datetime | None = None) -> str:
    """A short branch-safe name from the goal's first words, and the minute it was given."""
    words = "-".join(re.findall(r"[a-z0-9]+", goal.lower())[:6])[:40].strip("-")
    return f"{words or 'goal'}-{(now or datetime.now(UTC)).strftime('%m%d-%H%M')}"


def _registered(root: str | Path) -> set[Path]:
    listing = _git(root, "worktree", "list", "--porcelain")
    return {
        Path(line[len("worktree ") :]).resolve()
        for line in listing.splitlines()
        if line.startswith("worktree ")
    }


def _branch_exists(root: str | Path, branch: str) -> bool:
    try:
        _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    except WorktreeError:
        return False
    return True


def create(root: str, goal: str, *, now: datetime | None = None) -> tuple[str, str]:
    """Add a worktree for ``goal``; returns ``(branch, path)``.

    Stale state is cleared first: a registration whose folder is gone is pruned, and a leftover
    folder git does not know (a half-made worktree) is removed. A name already taken by a live
    branch or worktree gets a numeric suffix."""
    _git(root, "rev-parse", "--verify", "HEAD")  # a repository with a commit to branch from
    _git(root, "worktree", "prune")
    base = slug(goal, now=now)
    registered = _registered(root)
    for n in range(1, 100):
        name = base if n == 1 else f"{base}-{n}"
        path = Path(root) / ".cuttlefish" / "worktrees" / name
        branch = f"cuttlefish/{name}"
        if path.resolve() in registered or _branch_exists(root, branch):
            continue
        if path.exists():
            shutil.rmtree(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _git(root, "worktree", "add", "-b", branch, str(path))
        return branch, str(path)
    raise WorktreeError("could not find a free worktree name")


def discard(root: str, branch: str, path: str) -> None:
    """Undo ``create`` for a goal that never started."""
    _git(root, "worktree", "remove", "--force", path)
    _git(root, "branch", "-D", branch)
