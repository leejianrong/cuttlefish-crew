"""Open a pull request for a goal's branch with the machine's own ``gh`` login (CUT-241, ADR-0049).

cuttlefish holds no forge credential and makes no model call: the title and body are the goal's
text, the digest lines the page already shows and the commit subjects, run through the journal's
redactor. A refusal is a :class:`PullRequest` with a ``code`` and a plain ``reason``, not an
exception, so the page can say why and offer the one fix that exists (``not_pushed``: push).
Never merges. A push goes through the never-allowed list first, like any agent's push (CUT-240).
"""

from __future__ import annotations

import dataclasses
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from cuttlefish.delegate.never_allowed import never_allowed_reason, repo_default_branches

_TIMEOUT_S = 30
_PUSH_TIMEOUT_S = 120
_MAX_COMMITS = 20
_URL = re.compile(r"https?://\S+")
#: What ``gh`` and ``git`` need to use the person's own login and nothing else of the daemon's.
_ENV_KEYS = (
    "PATH", "HOME", "USER", "LANG", "XDG_CONFIG_HOME", "SSH_AUTH_SOCK",
    "GH_CONFIG_DIR", "GH_HOST", "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN",
)  # fmt: skip


@dataclasses.dataclass(frozen=True)
class PullRequest:
    ok: bool
    url: str | None = None
    #: A pull request for this branch was already open; ``url`` is that one.
    existing: bool = False
    #: Why not, in a word the page switches on: ``gh_missing``, ``not_logged_in``, ``no_branch``,
    #: ``default_branch``, ``no_remote``, ``no_commits``, ``not_pushed``, ``push_failed`` or
    #: ``gh_failed``.
    code: str | None = None
    reason: str | None = None
    branch: str | None = None
    base: str | None = None
    title: str | None = None

    def to_json(self) -> dict[str, object]:
        return {k: v for k, v in dataclasses.asdict(self).items() if v is not None}


def _refuse(
    code: str, reason: str, *, branch: str | None = None, base: str | None = None
) -> PullRequest:
    return PullRequest(False, code=code, reason=reason, branch=branch, base=base)


def _env() -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_KEYS if k in os.environ}
    return {**env, "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"}


def _run(
    argv: Sequence[str], cwd: Path, timeout: int = _TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(argv), cwd=cwd, env=_env(), capture_output=True, text=True,
            timeout=timeout, check=False,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(list(argv), 127, "", str(exc))


def _git(cwd: Path, *args: str, timeout: int = _TIMEOUT_S) -> str | None:
    done = _run(["git", "-c", "core.fsmonitor=false", "--no-pager", *args], cwd, timeout)
    return done.stdout.strip() if done.returncode == 0 else None


def _base(root: Path, defaults: frozenset[str]) -> str | None:
    """The default branch: what ``origin/HEAD`` names, else ``main`` or ``master`` if one exists."""
    named = _git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if named:
        return named.removeprefix("origin/")
    for name in ("main", "master"):
        if name in defaults and (
            _git(root, "rev-parse", "--verify", "-q", f"refs/heads/{name}")
            or _git(root, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{name}")
        ):
            return name
    return None


def _text(goal: str, summary: Sequence[str], subjects: Sequence[str]) -> tuple[str, str]:
    """The title and body: the goal, what happened (the page's digest) and the commit subjects."""
    first = goal.strip().splitlines()[0] if goal.strip() else "Work from cuttlefish-crew"
    title = first if len(first) <= 72 else first[:71] + "…"
    parts = [goal.strip()] if goal.strip() else []
    if summary:
        parts.append("## What happened\n\n" + "\n".join(f"- {line}" for line in summary))
    if subjects:
        parts.append("## Commits\n\n" + "\n".join(f"- {s}" for s in subjects))
    return title, "\n\n".join(parts)


def open_pull_request(
    root: str,
    goal: Mapping[str, str] | None,
    summary: Sequence[str],
    *,
    push: bool,
    scrub: Callable[[str], str] = lambda text: text,
) -> PullRequest:
    """Open a pull request for the goal's branch (its worktree's, else the checkout's) with ``gh``.

    ``push`` pushes the branch to ``origin`` first when it is not there yet; without it an
    unpushed branch is refused as ``not_pushed``. ``scrub`` is applied to every word of the text
    sent to the forge. Blocking (git, gh): call it off the event loop."""
    if shutil.which("gh", path=_env().get("PATH")) is None:
        return _refuse("gh_missing", "The GitHub CLI (gh) is not installed on this machine.")
    root_path = Path(root)
    if _run(["gh", "auth", "status"], root_path).returncode != 0:
        return _refuse("not_logged_in", "gh is not logged in. Run `gh auth login` on this machine.")
    where = Path((goal or {}).get("worktree", ""))
    folder = where if (goal or {}).get("worktree") and where.is_dir() else root_path
    branch = (goal or {}).get("branch") or _git(folder, "rev-parse", "--abbrev-ref", "HEAD")
    if not branch or branch == "HEAD":
        return _refuse("no_branch", "There is no branch to open a pull request from.")
    defaults = repo_default_branches(root_path)
    if branch in defaults:
        return _refuse(
            "default_branch",
            f"{branch} is the default branch. Give the goal its own branch first.",
            branch=branch,
        )
    base = _base(root_path, defaults)
    if base is None or _git(folder, "remote", "get-url", "origin") is None:
        return _refuse(
            "no_remote", "This repository has no origin remote and default branch to open against."
        )
    upstream = f"origin/{base}" if _git(folder, "rev-parse", "-q", f"origin/{base}") else base
    ahead = _git(folder, "rev-list", "--count", f"{upstream}..{branch}")
    if not ahead or int(ahead) == 0:
        return _refuse("no_commits", f"{branch} has no commits ahead of {base}.", branch=branch)
    remote = _git(folder, "rev-parse", "-q", f"refs/remotes/origin/{branch}")
    if remote != _git(folder, "rev-parse", branch):
        if not push:
            return _refuse(
                "not_pushed", f"{branch} is not pushed to origin yet.", branch=branch, base=base
            )
        line = f"git push -u origin {branch}"
        if never_allowed_reason(line, default_branches=defaults):
            return _refuse("push_failed", f"Pushing {branch} is never allowed.", branch=branch)
        done = _run(
            ["git", "-c", "core.hooksPath=/dev/null", "push", "-u", "origin", branch],
            folder,
            _PUSH_TIMEOUT_S,
        )
        if done.returncode != 0:
            tail = (done.stderr or done.stdout).strip().splitlines()
            return _refuse(
                "push_failed", scrub(tail[-1]) if tail else "git push failed", branch=branch
            )
    log = _git(folder, "log", f"-n{_MAX_COMMITS}", "--format=%s", f"{upstream}..{branch}") or ""
    title, body = _text(
        scrub((goal or {}).get("text", "")),
        [scrub(line) for line in summary],
        [scrub(s) for s in log.splitlines()],
    )
    title = scrub(title)
    done = _run(
        ["gh", "pr", "create", "--base", base, "--head", branch, "--title", title, "--body", body],
        folder,
    )
    output = f"{done.stdout}\n{done.stderr}"
    found = _URL.findall(output)
    if done.returncode == 0 and found:
        return PullRequest(True, url=found[-1], branch=branch, base=base, title=title)
    if "already exists" in output and found:
        return PullRequest(
            True, url=found[-1], existing=True, branch=branch, base=base, title=title
        )
    detail = (done.stderr.strip() or done.stdout.strip()).splitlines()[-1:] or ["gh failed"]
    reason = f"gh could not open the pull request: {scrub(detail[0])}"
    return _refuse("gh_failed", reason, branch=branch)
