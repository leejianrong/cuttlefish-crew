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
import statistics
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any, Literal

from cuttlefish.artifacts import Artifact
from cuttlefish.episodic.events import (
    AutopilotUpdated,
    DelegateApprovalsSet,
    FolderTrustAnswered,
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
    #: A refresh's advisory post-boundary measures (CUT-313, :func:`post_boundary`).
    handoff: dict[str, Any] | None = None

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
        elif isinstance(payload, DelegateApprovalsSet):
            choice = {None: "the daemon's setting", True: "on", False: "off"}[payload.value]
            found.append(Event(at, "note", f"Manager approvals for this project: {choice}."))
        elif isinstance(payload, FolderTrustAnswered):
            found.append(
                Event(
                    at,
                    "note",
                    f"cuttlefish trusted the goal's worktree for Claude Code: {payload.folder}",
                )
            )
        elif isinstance(payload, AutopilotUpdated) and payload.phase != previous:
            text = _autopilot_text(payload)
            if text:
                found.append(Event(at, "autopilot", text))
            previous = payload.phase
    return found


#: Reasons of a `running` update that say something happened to the run, not that it started.
_RUNNING_NOTES = (
    "Resumed",
    "The limit has reset",
    "The builder hit a usage limit",
    "The builder is working again",
)


def _autopilot_text(state: AutopilotUpdated) -> str:
    phase = state.phase
    if phase == "planning":
        return "You gave the builder a goal. It is planning."
    if phase == "awaiting_approval":
        return "The plan is ready and waiting for you."
    if phase == "limit_wait":
        return _clip(state.reason)  # "Usage limit reached. Will continue at ... UTC."
    if phase == "running":
        return (
            state.reason
            if state.reason.startswith(_RUNNING_NOTES)
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


#: Post-boundary windows (CUT-313): how many tool calls and minutes after a refresh count.
_FIRST_CALLS = 15
_PLAN_MINUTES = 30
_NUDGE_MINUTES = 10
_EDIT_TOOLS = ("Edit", "Write", "MultiEdit")


def _when(at: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return None


def nudge_times(events: Iterable[Any]) -> list[datetime]:
    """When Autopilot nudged the builder or gave up on it as stuck, from the journal."""
    times: list[datetime] = []
    nudges = 0
    for event in events:
        payload = event.payload
        if isinstance(payload, AutopilotUpdated):
            if payload.nudges > nudges or payload.phase == "stuck":
                times.append(event.ts)
            nudges = payload.nudges
    return times


def post_boundary(
    items: Sequence[Mapping[str, Any]],
    index: int,
    commits: Sequence[datetime] = (),
    nudges: Sequence[datetime] = (),
) -> dict[str, Any]:
    """What the builder did after the divider at ``items[index]``, read from the transcript, the
    commit times and the nudge times (CUT-313). All of it is a string or timing check and says
    nothing about whether the handoff was good; nothing is stored.

    ``repeated_*`` count distinct commands or edited paths in the first calls that the builder had
    already run before the boundary. ``plan_ticked`` is whether it touched the plan file soon
    after (``None`` when it never touches it). ``recovery_ratio`` is the time to its first commit
    or plan edit against the median gap between commits (``None`` below three commits)."""
    start = _when(items[index].get("at"))
    if start is None:
        return {}

    def calls(span: Sequence[Mapping[str, Any]]) -> list[tuple[str, str, datetime | None]]:
        return [
            (str(i.get("name")), str(i.get("summary") or ""), _when(i.get("at")))
            for i in span
            if i.get("kind") == "tool"
        ]

    nxt = next((n for n in range(index + 1, len(items)) if items[n].get("kind") == "divider"), None)
    before = {(n, s) for n, s, _ in calls(items[:index])}
    after = calls(items[index + 1 : nxt])
    first = {(n, s) for n, s, _ in after[:_FIRST_CALLS] if (n, s) in before}
    plan = [
        w
        for n, s, w in calls(items)
        if n in _EDIT_TOOLS and s.endswith("plan.md") and w is not None
    ]
    plan_after = [w for w in plan if w > start]
    ordered = sorted(commits)
    gaps = [(b - a).total_seconds() for a, b in pairwise(ordered)]
    first_move = min([*(c for c in commits if c > start), *plan_after], default=None)
    median = statistics.median(gaps) if len(gaps) >= 2 else 0
    recovery = None if first_move is None else (first_move - start).total_seconds()
    return {
        "repeated_commands": sum(1 for n, _ in first if n == "Bash"),
        "repeated_edits": sum(1 for n, _ in first if n in _EDIT_TOOLS),
        "plan_ticked": (
            any(w <= start + timedelta(minutes=_PLAN_MINUTES) for w in plan_after) if plan else None
        ),
        "recovery_s": None if recovery is None else round(recovery),
        "recovery_ratio": round(recovery / median, 1) if recovery is not None and median else None,
        "nudges": sum(1 for t in nudges if start < t <= start + timedelta(minutes=_NUDGE_MINUTES)),
    }


def transcript_events(
    items: Sequence[Mapping[str, Any]],
    commits: Sequence[datetime] = (),
    nudges: Sequence[datetime] = (),
) -> list[Event]:
    """Context refreshes, test runs and the builder's own recaps, from chat items. A refresh
    carries its advisory post-boundary measures (:func:`post_boundary`)."""
    calls: dict[str, str] = {}
    found: list[Event] = []
    for position, item in enumerate(items):
        kind, at = item.get("kind"), item.get("at")
        if not isinstance(at, str):
            continue
        if kind == "divider":
            found.append(
                Event(
                    at,
                    "refresh",
                    f"{item.get('text')}. The builder carries on from its own summary.",
                    handoff=post_boundary(items, position, commits, nudges),
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
