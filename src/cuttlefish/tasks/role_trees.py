"""The git side of a team's per-role worktrees, as durable satay tasks (CUT-239, ADR-0054).

Making worktrees and merging branches change the world, so each is a ``side_effect`` task whose
result is journaled: a replayed workflow reuses the recorded answer instead of doing it again, and
a task a crash interrupted reruns safely (``role_trees.prepare`` reuses a tree it already made).
Results are plain dicts so satay can record them.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import satay

from cuttlefish.sessions import role_trees
from cuttlefish.sessions.role_trees import RoleMerge, RoleTree, TeamTrees
from cuttlefish.sessions.worktree import WorktreeError, _git

_LOG = logging.getLogger(__name__)


def _encode(trees: TeamTrees) -> dict[str, Any]:
    return {
        "branch": trees.integration.branch,
        "path": trees.integration.path,
        "roles": {r: {"branch": t.branch, "path": t.path} for r, t in trees.roles.items()},
    }


def decode(data: dict[str, Any]) -> TeamTrees:
    return TeamTrees(
        RoleTree(data["branch"], data["path"]),
        {r: RoleTree(t["branch"], t["path"]) for r, t in data["roles"].items()},
    )


def _prepare(root: str, team_id: str, roles: list[str]) -> dict[str, Any]:
    try:
        _git(root, "rev-parse", "--verify", "HEAD")
    except WorktreeError:
        return {}  # not a repository with a commit: the team keeps today's shared root
    try:
        return _encode(role_trees.prepare(root, team_id, roles))
    except WorktreeError as exc:
        _LOG.warning("no per-role worktrees for team %s, using the shared root: %s", team_id, exc)
        return {}


@satay.task(side_effect=True)
async def prepare_role_trees(root: str, team_id: str, roles: list[str]) -> dict[str, Any]:
    """Make the team's branch and one worktree per role. ``{}`` when the project root is not a git
    repository with a commit, or the worktrees could not be made."""
    return await asyncio.to_thread(_prepare, root, team_id, roles)


def _merge(trees: dict[str, Any], roles: list[str]) -> dict[str, Any]:
    whole = decode(trees)
    part = TeamTrees(whole.integration, {r: whole.roles[r] for r in roles if r in whole.roles})
    try:
        results = role_trees.merge_back(part)
    except WorktreeError as exc:  # git itself failed; report it, never lose the run
        _LOG.warning("merging team branches failed: %s", exc)
        return {r: {"status": "conflict", "files": [f"(git failed: {exc})"]} for r in part.roles}
    return {r: {"status": m.status, "files": list(m.files)} for r, m in results.items()}


@satay.task(side_effect=True)
async def merge_role_trees(trees: dict[str, Any], roles: list[str]) -> dict[str, Any]:
    """Merge these roles' branches into the team's branch, in order. A conflict is aborted and
    listed, never resolved."""
    return await asyncio.to_thread(_merge, trees, roles)


def _finish(root: str, trees: dict[str, Any], results: dict[str, Any]) -> bool:
    whole = decode(trees)
    try:
        role_trees.remove_merged(
            root, whole, {r: RoleMerge(m["status"], tuple(m["files"])) for r, m in results.items()}
        )
    except WorktreeError as exc:
        _LOG.warning("could not remove a merged role's worktree: %s", exc)
        return False
    return True


@satay.task(side_effect=True)
async def finish_role_trees(root: str, trees: dict[str, Any], results: dict[str, Any]) -> bool:
    """Remove the worktrees of roles that merged or changed nothing. A conflicted role's stays, and
    so does the team's branch, which holds the merged result."""
    return await asyncio.to_thread(_finish, root, trees, results)
