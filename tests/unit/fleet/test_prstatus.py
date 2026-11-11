"""Unit: reading a goal's pull request and its checks with the machine's own gh (CUT-242), against a
fake gh on PATH that prints what a test puts in a file. No network, no forge."""

from __future__ import annotations

import json
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import satay.control
from starlette.testclient import TestClient

from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.prstatus import read_pr_status
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.fleet.tiers import Tier, required_tier
from cuttlefish.projects.store import ProjectStore

URL = "https://example.test/o/r/pull/9"
GOAL = {"text": "g", "at": "x", "branch": "cuttlefish/add-summary", "pr_url": URL}


class FakeGh:
    def __init__(self, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.out, self.err, self.log = tmp / "out.json", tmp / "err.txt", tmp / "gh.log"
        self.log.touch()
        gh = self.bin / "gh"
        gh.write_text(
            "#!/bin/sh\n"
            f'echo "$*" >> {self.log}\n'
            f"if [ -s {self.err} ]; then cat {self.err} >&2; exit 1; fi\n"
            f"cat {self.out}\n"
        )
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv("PATH", f"{self.bin}:/usr/bin:/bin")
        self.tmp = tmp

    def says(self, data: dict[str, Any]) -> None:
        self.out.write_text(json.dumps(data))

    def fails(self, text: str) -> None:
        self.err.write_text(text)

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()


@pytest.fixture
def gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    return FakeGh(tmp_path, monkeypatch)


def _check(name: str, conclusion: str | None, status: str = "COMPLETED") -> dict[str, Any]:
    return {
        "__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion,
        "detailsUrl": f"https://ci.test/{name}",
    }  # fmt: skip


def test_an_open_pr_with_a_failing_check_says_which(gh: FakeGh, tmp_path: Path) -> None:
    gh.says({
        "number": 9, "url": URL, "title": "Add summary", "state": "OPEN", "isDraft": False,
        "reviewDecision": "CHANGES_REQUESTED",
        "statusCheckRollup": [
            _check("lint", "SUCCESS"),
            _check("tests", "FAILURE"),
            _check("docs", None, "IN_PROGRESS"),
            _check("optional", "SKIPPED"),
            {"__typename": "StatusContext", "context": "ci/legacy", "state": "ERROR",
             "targetUrl": "https://ci.test/legacy"},
        ],
    })  # fmt: skip
    status = read_pr_status(str(tmp_path), GOAL)
    assert status.ok and (status.number, status.state, status.review) == (
        9, "open", "changes_requested",
    )  # fmt: skip
    assert status.checks_state == "failing"
    by_name = {c["name"]: c["state"] for c in status.checks}
    assert by_name == {
        "lint": "pass", "tests": "fail", "docs": "pending", "optional": "skipped",
        "ci/legacy": "fail",
    }  # fmt: skip
    assert status.checks[1]["url"] == "https://ci.test/tests"
    # The PR's own address is what gh is asked about, read-only, as argv.
    assert gh.calls() == [
        f"pr view {URL} --json number,url,title,state,isDraft,reviewDecision,statusCheckRollup"
    ]


def test_checks_are_passing_pending_or_none(gh: FakeGh, tmp_path: Path) -> None:
    base = {"number": 1, "url": URL, "title": "t", "state": "OPEN", "isDraft": True}
    gh.says({**base, "statusCheckRollup": [_check("a", "SUCCESS"), _check("b", "NEUTRAL")]})
    done = read_pr_status(str(tmp_path), GOAL)
    assert done.checks_state == "passing" and done.draft
    gh.says({**base, "statusCheckRollup": [_check("a", "SUCCESS"), _check("b", None, "QUEUED")]})
    assert read_pr_status(str(tmp_path), GOAL).checks_state == "pending"
    gh.says({**base, "statusCheckRollup": []})
    assert read_pr_status(str(tmp_path), GOAL).checks_state == "none"


def test_merged_and_closed_pull_requests(gh: FakeGh, tmp_path: Path) -> None:
    gh.says({"number": 1, "url": URL, "title": "t", "state": "MERGED", "statusCheckRollup": []})
    assert read_pr_status(str(tmp_path), GOAL).state == "merged"
    gh.says({"number": 1, "url": URL, "title": "t", "state": "CLOSED", "statusCheckRollup": []})
    assert read_pr_status(str(tmp_path), GOAL).state == "closed"


def test_no_pr_is_a_quiet_answer_not_an_error(gh: FakeGh, tmp_path: Path) -> None:
    gh.fails('no pull requests found for branch "cuttlefish/add-summary"')
    status = read_pr_status(str(tmp_path), {"text": "g", "at": "x", "branch": "cuttlefish/a"})
    assert not status.ok and status.code == "no_pr"
    assert gh.calls() == ["pr view cuttlefish/a --json number,url,title,state,isDraft,"
                          "reviewDecision,statusCheckRollup"]  # fmt: skip


def test_no_goal_no_gh_and_not_logged_in_have_plain_reasons(
    gh: FakeGh, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert read_pr_status(str(tmp_path), None).code == "no_goal"
    gh.fails("To get started with GitHub CLI, please run:  gh auth login")
    assert read_pr_status(str(tmp_path), GOAL).code == "not_logged_in"
    gh.err.write_text("")
    gh.out.write_text("not json")
    assert read_pr_status(str(tmp_path), GOAL).code == "gh_failed"
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert read_pr_status(str(tmp_path), GOAL).code == "gh_missing"


def test_a_flag_shaped_branch_or_url_is_never_handed_to_gh(gh: FakeGh, tmp_path: Path) -> None:
    status = read_pr_status(str(tmp_path), {"text": "g", "at": "x", "branch": "--web"})
    assert status.code == "no_branch" and gh.calls() == []
    bad = read_pr_status(str(tmp_path), {"text": "g", "at": "x", "pr_url": "--web"})
    assert bad.code == "no_branch" and gh.calls() == []


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = ProjectStore.open(tmp_path / "projects.db")
    app = create_app(FleetDaemon(store), security=satay.control.SecurityPolicy(token="t"))
    with TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "t"}) as c:
        c.store = store  # type: ignore[attr-defined]
        yield c
    store.close()


def test_the_route_is_view_tier_cached_and_says_when_a_project_is_on_a_worker(
    client: TestClient, gh: FakeGh, tmp_path: Path
) -> None:
    store = client.store  # type: ignore[attr-defined]
    root = tmp_path / "proj"
    (root / ".cuttlefish").mkdir(parents=True)
    (root / ".cuttlefish" / "goal.json").write_text(json.dumps({**GOAL}))
    pid = store.register(name="alpha", root=str(root)).id
    gh.says({"number": 9, "url": URL, "title": "t", "state": "OPEN", "statusCheckRollup": []})
    path = f"/api/projects/{pid}/goal/pull-request"
    assert required_tier("GET", path) == Tier.VIEW
    first = client.get(path).json()
    assert first["ok"] and first["state"] == "open"
    client.get(path)
    assert len(gh.calls()) == 1  # a second read within the cache window does not ask gh again
    assert client.get("/api/projects/nope/goal/pull-request").status_code == 404
    remote = store.register(name="remote", root=str(tmp_path / "x")).id
    store.update_worker(remote, "w1")
    assert client.get(f"/api/projects/{remote}/goal/pull-request").json()["code"] == "on_worker"
