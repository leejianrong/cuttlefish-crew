"""Unit: the timeline and the "while you were away" digest (CUT-176)."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from cuttlefish.artifacts import Artifact
from cuttlefish.episodic.events import AutopilotUpdated, RequestRaised
from cuttlefish.timeline import (
    MAX_EVENTS,
    Event,
    artifact_events,
    build,
    digest,
    journal_events,
    read_commits,
    transcript_events,
)


@dataclass
class _Journal:
    ts: datetime
    payload: Any


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 22, hour, minute, tzinfo=UTC)


def state(phase: str, reason: str = "", **extra: Any) -> AutopilotUpdated:
    return AutopilotUpdated(phase=phase, goal="g", backend="claude-code", reason=reason, **extra)


def test_autopilot_changes_of_phase_are_events_and_nudges_are_not() -> None:
    events = journal_events(
        [
            _Journal(at(9), state("planning")),
            _Journal(at(9, 5), state("awaiting_approval")),
            _Journal(at(9, 12), state("running", "The plan was approved.")),
            _Journal(at(10), state("running", "Nudged the builder.", nudges=1)),
            _Journal(at(11), state("running", "Nudged the builder.", nudges=2)),
            _Journal(at(12), state("paused", "Paused by you.")),
            _Journal(at(13), state("running", "Resumed by you.")),
            _Journal(at(14), state("done", "Every step of the plan is ticked.")),
        ]
    )
    assert [e.text for e in events] == [
        "You gave the builder a goal. It is planning.",
        "The plan is ready and waiting for you.",
        "You approved the plan. Autopilot started.",
        "Autopilot was paused.",
        "Resumed by you.",
        "Autopilot ended: Every step of the plan is ticked.",
    ]
    assert {e.kind for e in events} == {"autopilot"}


def test_a_request_is_a_needs_you_event_that_points_at_the_card() -> None:
    raised = RequestRaised(
        request_id="r1",
        kind="permission",
        title="Claude Code wants to use Bash",
        detail="git push",
        why="",
        answers=["allow_once", "deny"],
        expires_at="x",
    )
    event = journal_events([_Journal(at(15, 20), raised)])[0]
    assert (event.kind, event.ref, event.at) == ("needs_you", "r1", "2026-10-22T15:20:00Z")
    assert "Claude Code wants to use Bash" in event.text


def test_a_picture_a_video_and_a_design_are_events_and_a_log_is_not() -> None:
    def artifact(name: str, kind: str) -> Artifact:
        return Artifact(path=name, name=name, kind=kind, size=1, modified=at(14, 31).timestamp())

    events = artifact_events(
        [artifact("c.png", "image"), artifact("w.mp4", "video"), artifact("d.html", "html"),
         artifact("plan.md", "markdown"), artifact("x.bin", "other")]
    )  # fmt: skip
    assert [e.text for e in events] == [
        "Produced an image: c.png",
        "Produced a video: w.mp4",
        "Produced a design: d.html",
    ]
    assert events[0].ref == "c.png"


def _chat(kind: str, at_: str = "2026-10-22T13:40:00Z", **fields: Any) -> dict[str, Any]:
    return {"kind": kind, "at": at_, **fields}


def test_a_test_run_a_refresh_and_a_recap_come_from_the_transcript() -> None:
    events = transcript_events(
        [
            _chat("tool", tool_id="t1", name="Bash", summary="uv run pytest -q"),
            _chat("tool_result", tool_id="t1", text="..........\n41 passed, 2 failed in 0.6s"),
            _chat("tool", tool_id="t2", name="Bash", summary="ls -la"),
            _chat("tool_result", tool_id="t2", text="3 passed"),  # not a test command
            _chat("divider", text="Context was compacted"),
            _chat("note", text="Goal: summary. Tests were failing on empty months."),
        ]
    )
    texts = [e.text for e in events]
    assert "Tests: 41 passed, 2 failed." in texts
    assert not any("3 passed" in t for t in texts)
    assert any(t.startswith("Context was compacted") for t in texts)
    assert any(t.startswith("The builder's recap: Goal: summary") for t in texts)


def test_events_are_merged_newest_first_and_capped() -> None:
    old = [
        Event(f"2026-10-01T00:00:{n % 60:02d}Z", "commit", f"c{n}") for n in range(MAX_EVENTS + 20)
    ]
    new = [Event("2026-10-22T00:00:00Z", "image", "new")]
    merged = build(old, new)
    assert merged[0].text == "new" and len(merged) == MAX_EVENTS


def test_the_digest_says_what_happened_since_a_time_and_what_waits() -> None:
    events = [
        Event("2026-10-22T15:00:00Z", "commit", "Commit: b"),
        Event("2026-10-22T14:00:00Z", "commit", "Commit: a"),
        Event("2026-10-22T13:40:00Z", "tests", "Tests: 41 passed."),
        Event("2026-10-22T12:00:00Z", "refresh", "Context was compacted."),
        Event("2026-10-22T11:00:00Z", "image", "Produced an image: c.png", "c.png"),
        Event("2026-10-22T10:00:00Z", "note", "The builder's recap: it went well."),
        Event("2026-10-21T01:00:00Z", "commit", "Commit: before"),  # before the visit
    ]
    lines = digest(events, "2026-10-22T09:00:00Z", "feat/x", waiting=1)
    assert lines == [
        "2 commits on feat/x.",
        "Last test run: 41 passed.",
        "Refreshed its context 1 time.",
        "Left 1 file to look at, the newest c.png.",
        "The builder's recap: it went well.",
        "1 thing is waiting for you.",
    ]


def test_a_quiet_stretch_has_an_empty_digest_except_for_what_waits() -> None:
    assert digest([], "2026-10-22T09:00:00Z", None, waiting=0) == []
    assert digest([], "2026-10-22T09:00:00Z", None, waiting=3) == ["3 things are waiting for you."]


# -- git ----------------------------------------------------------------------------------------

pytestmark_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git(root: Path, *args: str) -> None:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "PATH": os.environ["PATH"],
        "HOME": str(root),
    }
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


@pytestmark_git
def test_commits_and_the_branch_are_read_from_the_repository(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "feat/ledger")
    (tmp_path / "a.txt").write_text("a")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "core: parse and group by month")
    branch, commits = read_commits(
        str(tmp_path), {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    )
    assert branch == "feat/ledger"
    assert [c.text for c in commits] == ["Commit: core: parse and group by month"]
    assert commits[0].kind == "commit" and commits[0].ref


@pytestmark_git
def test_no_repository_is_no_commits_not_an_error(tmp_path: Path) -> None:
    assert read_commits(str(tmp_path), {"PATH": os.environ["PATH"]}) == (None, [])
    assert read_commits(str(tmp_path / "missing"), {"PATH": os.environ["PATH"]}) == (None, [])


@pytestmark_git
def test_a_repository_that_names_a_helper_in_its_config_does_not_get_it_run(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.txt").write_text("a")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "one")
    marker = tmp_path / "ran"
    helper = tmp_path / "helper.sh"
    helper.write_text(f"#!/bin/sh\ntouch {marker}\n")
    helper.chmod(0o755)
    with (tmp_path / ".git" / "config").open("a") as config:
        config.write(
            f"[core]\n\tfsmonitor = {helper}\n\tpager = {helper}\n[pager]\n\tlog = {helper}\n"
        )
    read_commits(str(tmp_path), {"PATH": os.environ["PATH"], "HOME": str(tmp_path)})
    assert not marker.exists()
