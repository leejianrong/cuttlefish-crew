"""The project's timeline and "while you were away" digest (CUT-176, ADR-0044).

Both are derived, never stored (ADR-0004): from the episodic journal (Autopilot's changes and the
requests a person was asked about), the artifacts folder, the repository's own commits, and the
builder's transcript (a context refresh, a test run, its own recap). Each source is a small function
that returns :class:`Event` values; :func:`build` merges them newest first, and :func:`digest` says
what happened since a time in a few plain lines. Pure apart from :func:`read_commits`, the one call
out to ``git``.
"""

from __future__ import annotations

import dataclasses
import re
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from cuttlefish.artifacts import Artifact
from cuttlefish.episodic.events import (
    AutopilotUpdated,
    PullRequestOpened,
    RequestRaised,
    ScheduleRan,
    ScheduleSkipped,
)

Kind = Literal["needs_you", "autopilot", "refresh", "image", "tests", "commit", "note"]

#: The most events returned, newest first.
MAX_EVENTS = 80
_COMMITS = 40
_GIT_TIMEOUT_S = 5
#: How a build, test or CI command shows in a Bash call's one-line summary.
_TEST_COMMAND = re.compile(
    r"\b(pytest|vitest|jest|mocha|go test|cargo test|npm (?:run )?test|pnpm (?:run )?test|"
    r"yarn test|make (?:test|ci|check)|ctest|rspec)\b"
)
_PASSED = re.compile(r"(\d+)\s+passed")
_FAILED = re.compile(r"(\d+)\s+failed")


@dataclasses.dataclass(frozen=True)
class Event:
    #: ISO 8601 in UTC.
    at: str
    kind: Kind
    text: str
    #: Where to look: an artifact path for an image, a commit id for a commit.
    ref: str | None = None

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def iso(moment: datetime) -> str:
    """``moment`` as ISO 8601 in UTC with a ``Z``, the form every event here carries."""
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clip(text: str, limit: int = 160) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


# -- the journal --------------------------------------------------------------------------------


def journal_events(events: Iterable[Any]) -> list[Event]:
    """Needs-you requests and Autopilot's changes of phase, from the session's journal events."""
    found: list[Event] = []
    previous = ""
    for event in events:
        payload = event.payload
        at = iso(event.ts)
        if isinstance(payload, RequestRaised):
            found.append(
                Event(at, "needs_you", f"Needs you: {_clip(payload.title)}", payload.request_id)
            )
        elif isinstance(payload, PullRequestOpened):
            found.append(Event(at, "note", f"Pull request: {payload.url}", payload.url))
        elif isinstance(payload, ScheduleRan):
            found.append(Event(at, "note", "A schedule ran."))
        elif isinstance(payload, ScheduleSkipped):
            found.append(Event(at, "note", f"A schedule was skipped: {_clip(payload.reason)}"))
        elif isinstance(payload, AutopilotUpdated) and payload.phase != previous:
            text = _autopilot_text(payload)
            if text:
                found.append(Event(at, "autopilot", text))
            previous = payload.phase
    return found


def _autopilot_text(state: AutopilotUpdated) -> str:
    phase = state.phase
    if phase == "planning":
        return "You gave the builder a goal. It is planning."
    if phase == "awaiting_approval":
        return "The plan is ready and waiting for you."
    if phase == "running":
        return (
            state.reason
            if state.reason.startswith("Resumed")
            else "You approved the plan. Autopilot started."
        )
    if phase == "paused":
        return "Autopilot was paused."
    if phase == "stopped":
        return "Autopilot was stopped."
    return _clip(f"Autopilot ended: {state.reason}") if state.reason else f"Autopilot: {phase}."


# -- artifacts ----------------------------------------------------------------------------------


def artifact_events(artifacts: Iterable[Artifact]) -> list[Event]:
    """A file the builder left for a person: an image, a video or an HTML design."""
    words = {"image": "an image", "video": "a video", "html": "a design"}
    return [
        Event(
            iso(datetime.fromtimestamp(a.modified, UTC)),
            "image",
            f"Produced {words[a.kind]}: {a.name}",
            a.path,
        )
        for a in artifacts
        if a.kind in words
    ]


# -- the repository -----------------------------------------------------------------------------


def read_commits(root: str, env: Mapping[str, str]) -> tuple[str | None, list[Event]]:
    """The checked-out branch and its recent commits, or ``(None, [])`` where there is no
    repository or ``git`` fails. ``env`` is the agents' filtered environment (ADR-0029): the
    repository is one the agent writes to, so nothing of the daemon's own goes with the call, and
    git is told not to run any helper the repository's config names."""
    base = ["git", "-c", "core.fsmonitor=false", "--no-pager", "-C", root]
    git_env = {**env, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"}

    def run(*args: str) -> str | None:
        try:
            done = subprocess.run(
                [*base, *args],
                capture_output=True,
                text=True,
                check=False,
                timeout=_GIT_TIMEOUT_S,
                env=git_env,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None

    branch = run("rev-parse", "--abbrev-ref", "HEAD")
    log = run("log", f"-n{_COMMITS}", "--no-textconv", "--format=%h%x1f%ct%x1f%s")
    if branch is None or log is None:
        return None, []
    commits: list[Event] = []
    for line in log.splitlines():
        parts = line.split("\x1f", 2)
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        commits.append(
            Event(
                iso(datetime.fromtimestamp(int(parts[1]), UTC)),
                "commit",
                f"Commit: {_clip(parts[2], 120)}",
                parts[0],
            )
        )
    return branch.strip() or None, commits


# -- the transcript -----------------------------------------------------------------------------


def transcript_events(items: Sequence[Mapping[str, Any]]) -> list[Event]:
    """Context refreshes, test runs and the builder's own recaps, from chat items."""
    calls: dict[str, str] = {}
    found: list[Event] = []
    for item in items:
        kind, at = item.get("kind"), item.get("at")
        if not isinstance(at, str):
            continue
        if kind == "divider":
            found.append(
                Event(
                    at,
                    "refresh",
                    f"{item.get('text')}. The builder carries on from its own summary.",
                )
            )
        elif kind == "note" and isinstance(item.get("text"), str):
            found.append(Event(at, "note", f"The builder's recap: {_clip(item['text'], 200)}"))
        elif kind == "tool" and item.get("name") == "Bash" and isinstance(item.get("tool_id"), str):
            calls[item["tool_id"]] = str(item.get("summary") or "")
        elif (
            kind == "tool_result"
            and item.get("tool_id") in calls
            and _TEST_COMMAND.search(calls[item["tool_id"]])
        ):
            text = str(item.get("text") or "")
            passed, failed = _PASSED.search(text), _FAILED.search(text)
            if passed or failed:
                parts = ([f"{passed.group(1)} passed"] if passed else []) + (
                    [f"{failed.group(1)} failed"] if failed else []
                )
                found.append(Event(at, "tests", f"Tests: {', '.join(parts)}."))
    return found


# -- putting it together ------------------------------------------------------------------------


def build(*sources: Iterable[Event]) -> list[Event]:
    """Every source merged, newest first, at most :data:`MAX_EVENTS`."""
    merged = [event for source in sources for event in source]
    merged.sort(key=lambda e: e.at, reverse=True)
    return merged[:MAX_EVENTS]


def digest(events: Sequence[Event], since: str, branch: str | None, waiting: int) -> list[str]:
    """What happened since ``since`` (ISO), in a few plain lines. ``waiting`` is the count of things
    that need a person now, which is stated even when nothing else is."""
    recent = [e for e in events if e.at > since]
    lines: list[str] = []
    commits = [e for e in recent if e.kind == "commit"]
    tests = next((e for e in recent if e.kind == "tests"), None)  # the newest run
    if commits:
        where = f" on {branch}" if branch else ""
        lines.append(f"{len(commits)} {'commit' if len(commits) == 1 else 'commits'}{where}.")
    if tests:
        lines.append(tests.text.replace("Tests: ", "Last test run: "))
    refreshes = sum(1 for e in recent if e.kind == "refresh")
    if refreshes:
        lines.append(f"Refreshed its context {refreshes} {'time' if refreshes == 1 else 'times'}.")
    made = [e for e in recent if e.kind == "image"]
    if made:
        newest = made[0].text.split(": ", 1)[-1]
        noun = "file" if len(made) == 1 else "files"
        lines.append(f"Left {len(made)} {noun} to look at, the newest {newest}.")
    note = next((e for e in recent if e.kind == "note"), None)
    if note:
        lines.append(note.text)
    ended = next(
        (e for e in recent if e.kind == "autopilot" and e.text.startswith("Autopilot ended")), None
    )
    if ended:
        lines.append(ended.text)
    if waiting:
        lines.append(f"{waiting} {'thing is' if waiting == 1 else 'things are'} waiting for you.")
    return lines
