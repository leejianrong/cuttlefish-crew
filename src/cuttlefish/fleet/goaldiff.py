"""A goal branch's diff, read from git on demand (CUT-289, ADR-0048, ADR-0004).

Nothing is stored: the diff is ``git diff`` of the goal's worktree against the point the branch left
the default branch, plus the files git does not track yet (what the builder has made and not
committed). Read-only git only (``diff``, ``merge-base``, ``ls-files``, ``check-ignore``), with
``--no-ext-diff --no-textconv`` so a repository's own config runs nothing, and every ref and path
passed as an argv element, never through a shell.

What is withheld, on purpose: a changed file that looks like a secret (``.env``, keys, a
``.cuttlefish/`` file) or that the repository's own ignore list names has its name and counts shown
and its lines left out, because a diff is View tier and View is the lowest a paired phone gets.
Untracked files are only those git does not ignore. Nothing else is redacted: it is the person's
own code, shown on their own dashboard.

Bounded: at most ``MAX_FILES`` files, ``MAX_FILE_LINES`` lines of one file and ``MAX_PATCH_BYTES``
of patch; whatever is cut says so (``truncated``, ``omitted``). A refusal is a result with a
``code`` and a plain ``reason``, like a pull request's. Blocking: call it off the event loop.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from cuttlefish.delegate.never_allowed import repo_default_branches
from cuttlefish.fleet.pullrequest import _base, _env, _git, _run

MAX_FILES = 300
MAX_FILE_LINES = 1000
MAX_PATCH_BYTES = 1_500_000
MAX_UNTRACKED_BYTES = 200_000
_TIMEOUT_S = 30
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)")
_NUMSTAT = re.compile(r"^(\d+|-)\t(\d+|-)\t(.*)$", re.DOTALL)
_SECRET_NAMES = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore", "id_rsa*", "id_dsa*",
    "id_ecdsa*", "id_ed25519*", ".npmrc", ".netrc", ".pypirc", "credentials*", "secrets.*",
)  # fmt: skip
_NOT_SECRET = (".env.example", ".env.sample", ".env.template", ".env.dist")
_DIFF = [
    "git",
    "-c",
    "core.fsmonitor=false",
    "--no-pager",
    "diff",
    "--no-ext-diff",
    "--no-textconv",
]


@dataclasses.dataclass(frozen=True)
class GoalDiff:
    ok: bool
    code: str | None = None
    reason: str | None = None
    branch: str | None = None
    base: str | None = None
    #: The branch's tip, so a page (or an annotation) can tell the diff it read has moved.
    head: str | None = None
    #: ``worktree`` (committed and uncommitted work) or ``branch`` (the branch's commits only, as
    #: when its worktree has been removed).
    mode: str | None = None
    dirty: bool = False
    files: Sequence[Mapping[str, Any]] = ()
    #: How many files differ in all (``files`` may hold fewer, or none for a stat).
    files_changed: int = 0
    additions: int = 0
    deletions: int = 0
    #: Something was left out: more files than ``MAX_FILES`` or more patch than ``MAX_PATCH_BYTES``.
    truncated: bool = False
    note: str | None = None

    def to_json(self) -> dict[str, object]:
        out = {k: v for k, v in dataclasses.asdict(self).items() if v not in (None, False)}
        return out | {"ok": self.ok}


def _refuse(code: str, reason: str, branch: str | None = None, base: str | None = None) -> GoalDiff:
    return GoalDiff(False, code=code, reason=reason, branch=branch, base=base)


def looks_secret(path: str) -> bool:
    """Whether a path's name says its contents are a secret (``.env`` but not ``.env.example``)."""
    parts = path.split("/")
    if ".cuttlefish" in parts[:-1] or ".satay" in parts[:-1]:
        return True
    name = parts[-1].lower()
    if name in _NOT_SECRET:
        return False
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in _SECRET_NAMES)


def _ignored(folder: Path, paths: Sequence[str]) -> set[str]:
    """Which of ``paths`` the repository's ignore rules name (a tracked file can be one)."""
    if not paths:
        return set()
    done = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "check-ignore", "--no-index", "-z", "--stdin"],
        cwd=folder, input="\0".join(paths) + "\0", env=_env(), capture_output=True, text=True,
        timeout=_TIMEOUT_S, check=False,
    )  # fmt: skip
    return {p for p in done.stdout.split("\0") if p}


def _numstat(raw: str) -> list[tuple[str | None, str, int, int, bool]]:
    """``(old path or None, path, additions, deletions, binary)`` per file, in git's order."""
    out: list[tuple[str | None, str, int, int, bool]] = []
    tokens = raw.split("\0")
    i = 0
    while i < len(tokens):
        match = _NUMSTAT.match(tokens[i])
        i += 1
        if not match:
            continue
        add, dele, path = match.groups()
        old: str | None = None
        if path == "" and i + 1 < len(tokens):  # a rename: the two paths follow as own tokens
            old, path = tokens[i], tokens[i + 1]
            i += 2
        binary = add == "-"
        out.append((old, path, 0 if binary else int(add), 0 if binary else int(dele), binary))
    return out


def _blocks(patch: str) -> list[list[str]]:
    """The patch split per file. A body line always starts with a space, ``+``, ``-`` or ``\\``,
    so ``diff --git`` at the start of a line is always a header."""
    blocks: list[list[str]] = []
    for line in patch.split("\n"):
        if line.startswith("diff --git "):
            blocks.append([])
        if blocks:
            blocks[-1].append(line)
    return blocks


def _parse_block(lines: Sequence[str]) -> dict[str, Any]:
    status, binary = "modified", False
    hunks: list[dict[str, Any]] = []
    shown, truncated = 0, False
    old = new = 0
    for line in lines[1:]:
        if line.startswith("new file mode"):
            status = "added"
        elif line.startswith("deleted file mode"):
            status = "deleted"
        elif line.startswith("rename from"):
            status = "renamed"
        elif line.startswith(("Binary files ", "GIT binary patch")):
            binary = True
        elif match := _HUNK.match(line):
            old, new = int(match[1]), int(match[3])
            hunks.append({"header": match[5], "old_start": old, "new_start": new, "lines": []})
        elif hunks and line[:1] in (" ", "+", "-"):
            if shown >= MAX_FILE_LINES:
                truncated = True
                continue
            shown += 1
            kind = line[0]
            entry: dict[str, Any] = {"type": kind, "text": line[1:]}
            if kind != "+":
                entry["old"] = old
                old += 1
            if kind != "-":
                entry["new"] = new
                new += 1
            hunks[-1]["lines"].append(entry)
    return {"status": status, "binary": binary, "hunks": hunks, "truncated": truncated}


def _untracked(folder: Path, budget: int, *, lines: bool = True) -> list[dict[str, Any]]:
    """Files git does not track and does not ignore, shown as wholly added (``lines=False``: only
    the counts, for the stat)."""
    done = _run(["git", "ls-files", "--others", "--exclude-standard", "-z"], folder)
    out: list[dict[str, Any]] = []
    # cuttlefish's own folders are its state, not the project's work (git lists them if unexcluded).
    names = [
        p for p in done.stdout.split("\0") if p and not p.startswith((".cuttlefish/", ".satay/"))
    ]
    for rel in names[:budget]:
        target = folder / rel
        entry: dict[str, Any] = {
            "path": rel, "status": "untracked", "additions": 0, "deletions": 0,
            "binary": False, "hunks": [],
        }  # fmt: skip
        if looks_secret(rel):
            entry["withheld"] = "secret"
        elif target.is_symlink() or not target.is_file():
            entry["omitted"] = True
        else:
            with target.open("rb") as handle:
                data = handle.read(MAX_UNTRACKED_BYTES + 1)
            if b"\0" in data[:8000]:
                entry["binary"] = True
            else:
                rows = data[:MAX_UNTRACKED_BYTES].decode("utf-8", "replace").split("\n")
                if rows and rows[-1] == "":
                    rows.pop()
                entry["additions"] = len(rows)
                entry["truncated"] = len(data) > MAX_UNTRACKED_BYTES or len(rows) > MAX_FILE_LINES
                if lines:
                    shown = [
                        {"type": "+", "text": row, "new": n}
                        for n, row in enumerate(rows[:MAX_FILE_LINES], 1)
                    ]
                    entry["hunks"] = [
                        {"header": "", "old_start": 0, "new_start": 1, "lines": shown}
                    ]
        out.append(entry)
    return out


def _patch(folder: Path, args: Sequence[str]) -> tuple[str, bool]:
    """The patch text, and whether it was cut at ``MAX_PATCH_BYTES`` (git is stopped there)."""
    with subprocess.Popen(
        [*_DIFF, "--no-color", "-M", "-U3", *args],
        cwd=folder, env=_env(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    ) as proc:  # fmt: skip
        assert proc.stdout is not None
        data = proc.stdout.read(MAX_PATCH_BYTES + 1)
        cut = len(data) > MAX_PATCH_BYTES
        if cut:
            proc.kill()
        proc.wait(timeout=_TIMEOUT_S)
    return data[:MAX_PATCH_BYTES].decode("utf-8", "replace"), cut


@dataclasses.dataclass(frozen=True)
class _Target:
    folder: Path
    branch: str
    base: str
    tip: str
    #: The worktree (or the checkout, on this branch) exists, so uncommitted work is part of it.
    live: bool
    #: ``git diff`` arguments: the merge-base, the branch when there is no worktree to read.
    args: Sequence[str]


def _target(root: str, goal: Mapping[str, str] | None) -> _Target | GoalDiff:
    """Where a goal's branch is and what it is compared with, or the refusal that says why not."""
    if not goal:
        return _refuse("no_goal", "No goal has been given to this project's builder yet.")
    root_path = Path(root)
    where = Path(goal.get("worktree", ""))
    folder = where if goal.get("worktree") and where.is_dir() else root_path
    branch = goal.get("branch") or _git(folder, "rev-parse", "--abbrev-ref", "HEAD")
    if not branch or branch == "HEAD":
        return _refuse("no_branch", "There is no branch to show.")
    defaults = repo_default_branches(root_path)
    if branch in defaults:
        return _refuse(
            "default_branch",
            f"This goal works in the project's own checkout on {branch}, so there is no branch "
            "to compare. Turn on 'Its own branch' to see a goal's diff.",
            branch,
        )
    base = _base(root_path, defaults)
    if base is None:
        return _refuse("no_base", "This repository has no default branch to compare with.", branch)
    upstream = f"origin/{base}" if _git(folder, "rev-parse", "-q", f"origin/{base}") else base
    # `refs/heads/` makes the name a ref (which cannot start with `-`) before it is used below.
    tip = _git(folder, "rev-parse", "--verify", "-q", f"refs/heads/{branch}")
    if tip is None:
        return _refuse("no_branch", f"The branch {branch} no longer exists.", branch)
    mb = _git(folder, "merge-base", upstream, branch)
    if mb is None:
        return _refuse("no_base", f"{branch} shares no history with {base}.", branch)
    live = folder != root_path or _git(root_path, "rev-parse", "--abbrev-ref", "HEAD") == branch
    return _Target(
        folder, branch, base, tip, live, ["--end-of-options", mb, *([] if live else [branch]), "--"]
    )


def _read_numstat(t: _Target) -> list[tuple[str | None, str, int, int, bool]] | GoalDiff:
    done = _run([*_DIFF, "-M", "--numstat", "-z", *t.args], t.folder)
    if done.returncode != 0:
        return _refuse("git_failed", "git could not read the diff.", t.branch, t.base)
    return _numstat(done.stdout)


def goal_stat(root: str, goal: Mapping[str, str] | None) -> GoalDiff:
    """Only the counts (files, added, removed, uncommitted or not), without reading the patch: cheap
    enough to ask every few seconds, for a status line or a card (CUT-286)."""
    t = _target(root, goal)
    if isinstance(t, GoalDiff):
        return t
    listed = _read_numstat(t)
    if isinstance(listed, GoalDiff):
        return listed
    fresh = _untracked(t.folder, MAX_FILES, lines=False) if t.live else []
    return GoalDiff(
        True, branch=t.branch, base=t.base, head=t.tip, mode="worktree" if t.live else "branch",
        dirty=bool(fresh) or (t.live and bool(_git(t.folder, "status", "--porcelain", "-uno"))),
        files_changed=len(listed) + len(fresh),
        additions=sum(f[2] for f in listed) + sum(f["additions"] for f in fresh),
        deletions=sum(f[3] for f in listed),
    )  # fmt: skip


def goal_diff(root: str, goal: Mapping[str, str] | None) -> GoalDiff:
    """The diff of the goal's branch against the default branch it came from."""
    t = _target(root, goal)
    if isinstance(t, GoalDiff):
        return t
    listed = _read_numstat(t)
    if isinstance(listed, GoalDiff):
        return listed
    folder, live = t.folder, t.live
    patch, cut = _patch(folder, t.args)
    blocks = _blocks(patch)
    files: list[dict[str, Any]] = []
    for index, (old, path, add, dele, binary) in enumerate(listed[:MAX_FILES]):
        entry: dict[str, Any] = {"path": path, "additions": add, "deletions": dele}
        if old:
            entry["old_path"] = old
        if index < len(blocks) and not (cut and index == len(blocks) - 1):
            entry |= _parse_block(blocks[index])
        else:
            entry |= {"status": "modified", "hunks": [], "omitted": True}
        entry["binary"] = bool(entry.get("binary")) or binary
        files.append(entry)
    ignored = _ignored(folder, [f["path"] for f in files])
    for entry in files:
        if looks_secret(entry["path"]) or entry["path"] in ignored:
            entry |= {"hunks": [], "withheld": "secret", "omitted": False}
            entry.pop("truncated", None)
    dirty, fresh = False, []
    if live:
        fresh = _untracked(folder, max(0, MAX_FILES - len(files)))
        files += fresh
        dirty = bool(fresh) or bool(_git(folder, "status", "--porcelain", "-uno"))
    truncated = cut or len(listed) > MAX_FILES
    note = (
        f"Only part of this diff is shown ({len(listed)} files in all). "
        "Open the branch in your own tools for the rest."
        if truncated
        else None
    )
    return GoalDiff(
        True, branch=t.branch, base=t.base, head=t.tip, mode="worktree" if live else "branch",
        dirty=dirty, files=files, files_changed=len(listed) + len(fresh),
        additions=sum(f[2] for f in listed) + sum(f["additions"] for f in fresh),
        deletions=sum(f[3] for f in listed), truncated=truncated, note=note,
    )  # fmt: skip
