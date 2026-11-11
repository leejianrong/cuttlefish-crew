"""A goal's pull request and its checks, read with the machine's own ``gh`` (CUT-242, ADR-0049).

Read-only: one ``gh pr view --json`` for the PR the goal opened (its URL when the journal has it,
else its branch), run as argv with the same allowlisted environment as opening one. cuttlefish holds
no forge credential and never merges. No PR, no ``gh`` and a ``gh`` that is not logged in are quiet
answers (``ok: false`` and a ``code``), not errors, so a page can show nothing for them. Blocking:
call it off the event loop.
"""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cuttlefish.fleet.pullrequest import _env, _git, _run

_FIELDS = "number,url,title,state,isDraft,reviewDecision,statusCheckRollup"
_URL = re.compile(r"^https?://\S+$")
_FAIL = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}
_PASS = {"SUCCESS"}
_SKIP = {"NEUTRAL", "SKIPPED", "STALE"}
_REVIEW = {
    "APPROVED": "approved",
    "CHANGES_REQUESTED": "changes_requested",
    "REVIEW_REQUIRED": "review_required",
}


@dataclasses.dataclass(frozen=True)
class PrStatus:
    ok: bool
    #: Why not, in a word the page switches on: ``no_goal``, ``no_branch``, ``no_pr``,
    #: ``gh_missing``, ``not_logged_in``, ``gh_failed`` or (from the daemon) ``on_worker``.
    code: str | None = None
    reason: str | None = None
    number: int | None = None
    url: str | None = None
    title: str | None = None
    #: ``open``, ``merged`` or ``closed``.
    state: str | None = None
    draft: bool = False
    #: ``approved``, ``changes_requested`` or ``review_required``, when the repository asks for one.
    review: str | None = None
    #: ``passing``, ``failing``, ``pending`` or ``none``.
    checks_state: str | None = None
    #: Each check as ``{name, state, url}``, ``state`` one of ``pass``, ``fail``, ``pending``,
    #: ``skipped``.
    checks: tuple[Mapping[str, str], ...] = ()

    def to_json(self) -> dict[str, object]:
        out = {k: v for k, v in dataclasses.asdict(self).items() if v not in (None, False, ())}
        return out | {"ok": self.ok, "checks": list(self.checks)}


def _refuse(code: str, reason: str) -> PrStatus:
    return PrStatus(False, code=code, reason=reason)


def _check(raw: Mapping[str, Any]) -> dict[str, str]:
    """One entry of ``statusCheckRollup``: a check run, or a commit status from an older CI."""
    if raw.get("__typename") == "StatusContext":
        name, url = str(raw.get("context", "")), str(raw.get("targetUrl") or "")
        word = str(raw.get("state", "")).upper()
        state = "pass" if word in _PASS else "fail" if word in _FAIL else "pending"
    else:
        name, url = str(raw.get("name", "")), str(raw.get("detailsUrl") or "")
        word = str(raw.get("conclusion") or "").upper()
        if str(raw.get("status", "")).upper() != "COMPLETED":
            state = "pending"
        else:
            state = "pass" if word in _PASS else "skipped" if word in _SKIP else "fail"
    return {"name": name, "state": state, "url": url}


def _checks_state(checks: list[dict[str, str]]) -> str:
    states = {c["state"] for c in checks}
    if "fail" in states:
        return "failing"
    if "pending" in states:
        return "pending"
    return "passing" if states else "none"


def read_pr_status(root: str, goal: Mapping[str, str] | None) -> PrStatus:
    """The pull request the goal opened, with its checks and review state."""
    if not goal:
        return _refuse("no_goal", "No goal has been given to this project's builder yet.")
    target = goal.get("pr_url") or goal.get("branch") or ""
    # Never a flag: gh would read it as one. A URL starts with http(s); a branch never with `-`.
    if not target or target.startswith("-") or (":" in target and not _URL.match(target)):
        return _refuse("no_branch", "There is no pull request or branch to look up.")
    if shutil.which("gh", path=_env().get("PATH")) is None:
        return _refuse("gh_missing", "The GitHub CLI (gh) is not installed on this machine.")
    folder = Path(goal.get("worktree", ""))
    folder = folder if goal.get("worktree") and folder.is_dir() else Path(root)
    if not folder.is_dir() or _git(folder, "rev-parse", "--git-dir") is None:
        folder = Path(root)
    done = _run(["gh", "pr", "view", target, "--json", _FIELDS], folder)
    if done.returncode != 0:
        text = f"{done.stderr}\n{done.stdout}".lower()
        if "no pull requests found" in text:
            return _refuse("no_pr", "No pull request is open for this goal.")
        if "gh auth login" in text or "not logged" in text:
            return _refuse(
                "not_logged_in", "gh is not logged in. Run `gh auth login` on this machine."
            )
        return _refuse("gh_failed", "gh could not read the pull request.")
    try:
        data = json.loads(done.stdout)
        checks = [_check(c) for c in data.get("statusCheckRollup") or [] if isinstance(c, dict)]
        return PrStatus(
            True,
            number=int(data["number"]),
            url=str(data.get("url") or "") or None,
            title=str(data.get("title") or "") or None,
            state=str(data["state"]).lower(),
            draft=bool(data.get("isDraft")),
            review=_REVIEW.get(str(data.get("reviewDecision") or "")),
            checks_state=_checks_state(checks),
            checks=tuple(checks),
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return _refuse("gh_failed", "gh answered with something that is not a pull request.")
