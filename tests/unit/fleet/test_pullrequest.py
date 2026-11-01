"""Unit: opening a pull request with the machine's own gh (CUT-241, ADR-0049), against a fake gh
on PATH and a real repository with a bare local origin. No network, no forge."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from cuttlefish.episodic.redact import Redactor
from cuttlefish.fleet.pullrequest import open_pull_request
from cuttlefish.sessions import worktree

if TYPE_CHECKING:
    from conftest import PrRig

GOAL = {"text": "Add a summary command\n\nAnd tests for it.", "at": "2026-10-31T00:00:00Z"}
BRANCH = "cuttlefish/add-summary"


def test_a_pushed_branch_opens_and_the_text_is_the_goal_and_commits(pr_rig: PrRig) -> None:
    pr_rig.git("push", "-q", "-u", "origin", BRANCH)
    goal = {**GOAL, "branch": BRANCH}
    result = open_pull_request(str(pr_rig.root), goal, ["1 commit."], push=False)
    assert result.ok and result.url == "https://example.test/o/r/pull/9"
    assert (result.branch, result.base) == (BRANCH, "main")
    call = pr_rig.gh_calls()[-1]
    assert f"pr create --base main --head {BRANCH} --title Add a summary command" in call
    assert "And tests for it." in call and "1 commit." in call and "Add the summary command" in call


def test_an_unpushed_branch_is_refused_until_push_is_asked_for(pr_rig: PrRig) -> None:
    refused = open_pull_request(str(pr_rig.root), GOAL, [], push=False)
    assert not refused.ok and refused.code == "not_pushed"
    assert pr_rig.gh_calls() == ["auth status"]
    assert BRANCH not in pr_rig.git("branch", "--list", "-r")
    opened = open_pull_request(str(pr_rig.root), GOAL, [], push=True)
    assert opened.ok
    assert BRANCH in pr_rig.git("-C", str(pr_rig.bare), "branch", "--list")


def test_a_worktree_goal_branch_is_pushed_and_opened(pr_rig: PrRig) -> None:
    pr_rig.git("switch", "-q", "main")
    branch, path = worktree.create(str(pr_rig.root), "Add a summary")
    pr_rig.git("commit", "-q", "--allow-empty", "-m", "work", cwd=Path(path))
    goal = {**GOAL, "branch": branch, "worktree": path}
    result = open_pull_request(str(pr_rig.root), goal, [], push=True)
    assert result.ok and result.branch == branch
    assert branch in pr_rig.git("-C", str(pr_rig.bare), "branch", "--list")


def test_the_default_branch_is_refused(pr_rig: PrRig) -> None:
    pr_rig.git("switch", "-q", "main")
    result = open_pull_request(str(pr_rig.root), None, [], push=True)
    assert result.code == "default_branch"
    assert not any("pr create" in call for call in pr_rig.gh_calls())


def test_a_branch_with_no_commits_ahead_is_refused(pr_rig: PrRig) -> None:
    pr_rig.git("switch", "-q", "-c", "cuttlefish/empty", "main")
    result = open_pull_request(str(pr_rig.root), None, [], push=True)
    assert result.code == "no_commits"


def test_a_missing_gh_is_a_plain_reason(pr_rig: PrRig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(pr_rig.nogh))
    result = open_pull_request(str(pr_rig.root), GOAL, [], push=True)
    assert result.code == "gh_missing" and "not installed" in (result.reason or "")


def test_a_gh_that_is_not_logged_in_is_a_plain_reason(pr_rig: PrRig) -> None:
    pr_rig.mode.write_text("loggedout")
    result = open_pull_request(str(pr_rig.root), GOAL, [], push=True)
    assert result.code == "not_logged_in" and "gh auth login" in (result.reason or "")


def test_a_pull_request_already_open_is_reported_not_duplicated(pr_rig: PrRig) -> None:
    pr_rig.git("push", "-q", "-u", "origin", BRANCH)
    pr_rig.mode.write_text("exists")
    result = open_pull_request(str(pr_rig.root), GOAL, [], push=False)
    assert result.ok and result.existing and result.url == "https://example.test/o/r/pull/7"


def test_uncommitted_files_are_named_as_left_out(pr_rig: PrRig) -> None:
    pr_rig.git("push", "-q", "-u", "origin", BRANCH)
    clean = open_pull_request(str(pr_rig.root), GOAL, [], push=False)
    assert clean.ok and clean.warning is None
    (pr_rig.root / "scratch.txt").write_text("not committed")
    result = open_pull_request(str(pr_rig.root), GOAL, [], push=False)
    assert result.ok and result.warning and "1 uncommitted or untracked file" in result.warning
    assert result.warning in str(result.to_json())


def test_a_secret_in_the_goal_or_a_commit_never_reaches_the_pull_request_text(
    pr_rig: PrRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "planted-" + "z" * 20  # shaped like nothing a scanner knows
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    pr_rig.commit(f"Wire it up with {secret}")
    pr_rig.git("push", "-q", "-u", "origin", BRANCH)
    goal = {"text": f"Use {secret} to call the API", "at": GOAL["at"]}
    result = open_pull_request(
        str(pr_rig.root),
        goal,
        [f"tests used {secret}"],
        push=False,
        scrub=lambda text: Redactor().scrub(text)[0],
    )
    assert result.ok
    sent = "\n".join(pr_rig.gh_calls())
    assert secret not in sent and secret not in (result.title or "")
    assert "[redacted:OPENROUTER_API_KEY]" in sent
