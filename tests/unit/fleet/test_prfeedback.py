"""Unit: reading a pull request's review comments with the machine's own gh (CUT-293), against a
fake gh on PATH. Comments are other people's text: scrubbed, bounded, and only ever read here;
sending them to the builder is a person's button (the page), never this module."""

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
from cuttlefish.fleet.prstatus import MAX_COMMENT_CHARS, MAX_COMMENTS, read_pr_comments
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.fleet.tiers import Tier, required_tier
from cuttlefish.projects.store import ProjectStore

URL = "https://example.test/o/r/pull/9"
GOAL = {"text": "g", "at": "x", "branch": "cuttlefish/feat", "pr_url": URL}


class FakeGh:
    """`pr view` prints view.json and `api` prints api.json; both are logged."""

    def __init__(self, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.view, self.api, self.log = tmp / "view.json", tmp / "api.json", tmp / "gh.log"
        self.log.touch()
        self.api.write_text("[]")
        gh = self.bin / "gh"
        gh.write_text(
            "#!/bin/sh\n"
            f'echo "$*" >> {self.log}\n'
            f'if [ "$1" = api ]; then cat {self.api}; exit 0; fi\n'
            f"cat {self.view}\n"
        )
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv("PATH", f"{self.bin}:/usr/bin:/bin")

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()


@pytest.fixture
def gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    return FakeGh(tmp_path, monkeypatch)


def _one_comment(body: str) -> str:
    comment = {"author": {"login": "a"}, "body": body, "createdAt": "2026-11-12T10:00:00Z"}
    return json.dumps({"number": 9, "reviews": [], "comments": [comment]})


def _review(author: str, state: str, body: str, at: str) -> dict[str, Any]:
    return {"author": {"login": author}, "state": state, "body": body, "submittedAt": at}


def test_reviews_comments_and_inline_comments_come_back_in_time_order(
    gh: FakeGh, tmp_path: Path
) -> None:
    gh.view.write_text(json.dumps({
        "number": 9,
        "reviews": [
            _review("alice", "CHANGES_REQUESTED", "Please add tests.", "2026-11-12T10:00:00Z"),
            _review("bob", "APPROVED", "", "2026-11-12T11:00:00Z"),  # an empty approval: no text
        ],
        "comments": [{"author": {"login": "carol"}, "body": "Nit: rename.",
                      "createdAt": "2026-11-12T09:00:00Z", "url": URL + "#c1"}],
    }))  # fmt: skip
    gh.api.write_text(json.dumps([
        {"user": {"login": "alice"}, "body": "Use a set here.", "path": "src/a.py", "line": 12,
         "created_at": "2026-11-12T10:00:30Z", "html_url": URL + "#r1"},
        {"user": {"login": "alice"}, "body": "Old line.", "path": "src/b.py", "line": None,
         "original_line": 4, "created_at": "2026-11-12T10:00:40Z"},
    ]))  # fmt: skip
    done = read_pr_comments(str(tmp_path), GOAL)
    assert done.ok and done.number == 9
    assert [(c["kind"], c["author"], c["body"]) for c in done.comments] == [
        ("comment", "carol", "Nit: rename."),
        ("review", "alice", "Please add tests."),
        ("inline", "alice", "Use a set here."),
        ("inline", "alice", "Old line."),
    ]
    inline = done.comments[2]
    assert (inline["path"], inline["line"], inline["state"]) == ("src/a.py", "12", "")
    assert done.comments[1]["state"] == "changes_requested"
    assert done.comments[3]["line"] == "4"
    assert gh.calls() == [
        f"pr view {URL} --json number,reviews,comments",
        "api repos/{owner}/{repo}/pulls/9/comments --paginate",
    ]


def test_text_is_scrubbed_cut_and_bounded(gh: FakeGh, tmp_path: Path) -> None:
    many = [
        {"author": {"login": "a"}, "body": f"c{i}", "createdAt": f"2026-11-12T10:00:{i:02d}Z"}
        for i in range(MAX_COMMENTS + 5)
    ]
    many.append({"author": {"login": "a"}, "body": "x" * (MAX_COMMENT_CHARS + 50),
                 "createdAt": "2026-11-12T11:00:00Z"})  # fmt: skip
    many.append({"author": {"login": "a"}, "body": "my token is sk-secret-1",
                 "createdAt": "2026-11-12T12:00:00Z"})  # fmt: skip
    gh.view.write_text(json.dumps({"number": 9, "reviews": [], "comments": many}))
    done = read_pr_comments(str(tmp_path), GOAL, scrub=lambda t: t.replace("sk-secret-1", "[x]"))
    assert len(done.comments) == MAX_COMMENTS and done.omitted == 7
    assert done.comments[-1]["body"] == "my token is [x]"
    long = done.comments[-2]["body"]
    assert len(long) == MAX_COMMENT_CHARS + 1 and long.endswith("…")  # the newest are kept


def test_a_failing_inline_read_still_returns_the_rest(gh: FakeGh, tmp_path: Path) -> None:
    gh.view.write_text(_one_comment("hi"))
    gh.api.write_text("not json")
    done = read_pr_comments(str(tmp_path), GOAL)
    assert done.ok and [c["body"] for c in done.comments] == ["hi"]


def test_no_pr_or_no_target_is_quiet_and_a_flag_never_reaches_gh(
    gh: FakeGh, tmp_path: Path
) -> None:
    assert read_pr_comments(str(tmp_path), None).code == "no_goal"
    bad = read_pr_comments(str(tmp_path), {"text": "g", "at": "x", "pr_url": "--web"})
    assert bad.code == "no_branch" and gh.calls() == []
    gh.view.write_text("not json")
    assert read_pr_comments(str(tmp_path), GOAL).code == "gh_failed"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = ProjectStore.open(tmp_path / "projects.db")
    app = create_app(FleetDaemon(store), security=satay.control.SecurityPolicy(token="t"))
    with TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "t"}) as c:
        c.store = store  # type: ignore[attr-defined]
        yield c
    store.close()


def test_the_route_needs_approve_scrubs_and_says_when_a_project_is_on_a_worker(
    client: TestClient, gh: FakeGh, tmp_path: Path
) -> None:
    store = client.store  # type: ignore[attr-defined]
    root = tmp_path / "proj"
    (root / ".cuttlefish").mkdir(parents=True)
    (root / ".cuttlefish" / "goal.json").write_text(json.dumps(GOAL))
    pid = store.register(name="alpha", root=str(root)).id
    gh.view.write_text(_one_comment("hi"))
    path = f"/api/projects/{pid}/goal/pull-request/comments"
    # Free text written by other people, whose only use is the send button: Approve, not View.
    assert required_tier("GET", path) == Tier.APPROVE
    body = client.get(path).json()
    assert body["ok"] and body["comments"][0]["body"] == "hi"
    assert client.get("/api/projects/nope/goal/pull-request/comments").status_code == 404
    remote = store.register(name="remote", root=str(tmp_path / "x")).id
    store.update_worker(remote, "w1")
    assert client.get(f"/api/projects/{remote}/goal/pull-request/comments").json()["code"] == (
        "on_worker"
    )
