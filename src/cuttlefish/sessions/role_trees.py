"""A git worktree per team role, merged back through one integration branch (CUT-239, ADR-0054).

First slice, not yet called by ``team.py``: the git half of lifting the kopicode ``--root`` lock.
A team gets an integration branch ``cuttlefish/team-<id>`` in its own worktree, and each role a
branch ``cuttlefish/team-<id>-<role>`` in a worktree made from it, all under
``<root>/.cuttlefish/worktrees/`` (inside the project root, as ADR-0048 requires). ``merge_back``
merges each role's branch into the integration branch. A conflict is aborted and reported, never
resolved here, and the role's branch and worktree stay so the work is not lost. The checkout and
its branch are never touched.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from cuttlefish.sessions.worktree import WorktreeError, _branch_exists, _git, _registered


@dataclasses.dataclass(frozen=True)
class RoleTree:
    branch: str
    path: str


@dataclasses.dataclass(frozen=True)
class TeamTrees:
    integration: RoleTree
    roles: dict[str, RoleTree]


@dataclasses.dataclass(frozen=True)
class RoleMerge:
    #: ``merged``, ``nothing`` (the role changed nothing) or ``conflict``.
    status: str
    files: tuple[str, ...] = ()


def _safe(name: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", name.lower())) or "role"


def _add(root: str, name: str, start: str | None, made: list[RoleTree]) -> RoleTree:
    path = Path(root) / ".cuttlefish" / "worktrees" / name
    branch = f"cuttlefish/{name}"
    here, branched = path.resolve() in _registered(root), _branch_exists(root, branch)
    if here and branched:
        return RoleTree(branch, str(path))  # made by an earlier, interrupted run: reuse it
    if here or branched:
        raise WorktreeError(f"{name} already exists; remove it first")
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(root, "worktree", "add", "-b", branch, str(path), *([start] if start else []))
    made.append(RoleTree(branch, str(path)))
    return made[-1]


def prepare(root: str, team_id: str, roles: list[str]) -> TeamTrees:
    """Make the integration worktree from ``HEAD`` and one worktree per role from it. A failure
    part way removes what this call made. One a crashed earlier call already made is reused, so
    a resumed team keeps the work already in it."""
    _git(root, "rev-parse", "--verify", "HEAD")
    _git(root, "worktree", "prune")
    team = f"team-{_safe(team_id)}"
    names = [_safe(r) for r in roles]
    if len(set(names)) != len(names):
        raise WorktreeError("two roles have the same name once made branch-safe")
    made: list[RoleTree] = []
    try:
        integration = _add(root, team, None, made)
        trees = {}
        for role, safe in zip(roles, names, strict=True):
            trees[role] = _add(root, f"{team}-{safe}", integration.branch, made)
    except WorktreeError:
        for tree in reversed(made):
            _git(root, "worktree", "remove", "--force", tree.path)
            _git(root, "branch", "-D", tree.branch)
        raise
    return TeamTrees(integration, trees)


def merge_back(trees: TeamTrees) -> dict[str, RoleMerge]:
    """Commit what each role left in its worktree, then merge its branch into the integration
    branch, in role order. A conflicting merge is aborted and listed; the rest still merge."""
    out: dict[str, RoleMerge] = {}
    integration = trees.integration.path
    for role, tree in trees.roles.items():
        _git(tree.path, "add", "-A")
        if _git(tree.path, "status", "--porcelain").strip():
            _git(
                tree.path,
                "-c", "user.name=cuttlefish", "-c", "user.email=cuttlefish@localhost",
                "commit", "-q", "-m", f"{role}: work left uncommitted at the end of the round",
            )  # fmt: skip
        if not _git(integration, "rev-list", f"{trees.integration.branch}..{tree.branch}").strip():
            out[role] = RoleMerge("nothing")
            continue
        try:
            _git(
                integration,
                "-c", "user.name=cuttlefish", "-c", "user.email=cuttlefish@localhost",
                "merge", "--no-ff", "-m", f"Merge {role}", tree.branch,
            )  # fmt: skip
        except WorktreeError:
            files = tuple(_git(integration, "diff", "--name-only", "--diff-filter=U").split())
            _git(integration, "merge", "--abort")
            out[role] = RoleMerge("conflict", files)
        else:
            out[role] = RoleMerge("merged")
    return out


def remove_merged(root: str, trees: TeamTrees, results: dict[str, RoleMerge]) -> None:
    """Remove the worktrees and branches of roles that merged or changed nothing. A conflicted
    role's stay, and so does the integration branch's worktree, which holds the merged result."""
    for role, tree in trees.roles.items():
        if results[role].status != "conflict":
            _git(root, "worktree", "remove", "--force", tree.path)
            _git(root, "branch", "-D", tree.branch)
