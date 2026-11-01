"""Seed or clear 100 fake projects in a scratch HOME, for scale-testing the dashboard (CUT-187).

    uv run python scripts/seed_fleet.py seed  --home /tmp/scratch-home [--count 100]
    uv run python scripts/seed_fleet.py clear --home /tmp/scratch-home

Registers projects in ``<home>/.cuttlefish/projects.db`` and writes each one a small episodic
journal under ``<home>/seed-projects/<name>/.cuttlefish/``, in one of five states: working,
waiting (a round finished and a review is wanted), done, stuck (a role failed, or a round that
has been silent for hours) and idle (never run). No agent session is launched: the projects have
no persisted roles, so a daemon's resume skips them. Point ``cuttlefish serve`` at the same
HOME to look at them. The times are relative to when you seed; seed again after a while to
refresh a "working" project that has since gone quiet.

It refuses your real home directory: use a brand-new scratch one.
"""

from __future__ import annotations

import argparse
import os
import pwd
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cuttlefish.episodic.events import (
    DelegationCompleted,
    DelegationStarted,
    EventPayload,
    TaskCompleted,
    TaskFailed,
    TaskSubmitted,
)
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.projects.store import ProjectStore
from cuttlefish.roles import template_roles

PREFIX = "seed-"
_WORDS = ("billing", "docs", "api", "web", "mobile", "infra", "search", "auth", "etl", "ml")

#: (state, how many of every 100, minutes since the last event, min and max).
_PLAN: tuple[tuple[str, int, tuple[int, int]], ...] = (
    ("working", 30, (1, 20)),
    ("waiting", 15, (5, 240)),
    ("done", 30, (60, 2880)),
    ("stuck", 15, (200, 1800)),
    ("idle", 10, (0, 0)),
)


def _state_for(index: int, count: int) -> tuple[str, int]:
    """The state of project `index` of `count`, and a stable 0..n offset within its state."""
    edge = 0
    for state, share, _ in _PLAN:
        size = max(1, count * share // 100)
        if index < edge + size or state == "idle":
            return state, index - edge
        edge += size
    raise AssertionError("unreachable")


def _journal(state: str, offset: int, root: Path) -> tuple[list[tuple[EventPayload, int]], bool]:
    """The events (payload, minutes ago) of a journal in `state`, and whether it wants a review."""
    low, high = next(window for name, _, window in _PLAN if name == state)
    last = low + (high - low) * ((offset * 7) % 10) // 9
    task = TaskSubmitted("Work through the backlog.", role="builder")
    start = DelegationStarted("Work through the backlog.", str(root), role="builder")
    round_done = DelegationCompleted(
        "Finished a round.",
        ["src/app.py"],
        role="builder",
        tokens=4200 + offset * 90,
        cost_usd=0.05,
    )
    if state == "working":
        return [(task, last + 30), (start, last)], False
    if state == "waiting":
        return [(task, last + 30), (start, last + 10), (round_done, last)], True
    if state == "done":
        return [
            (task, last + 60),
            (start, last + 40),
            (round_done, last + 10),
            (TaskCompleted("Done.", role="builder"), last),
        ], False
    if offset % 2:
        return [(task, last + 30), (start, last)], False  # silent for hours
    return [
        (task, last + 30),
        (start, last + 10),
        (TaskFailed("Tests keep failing.", role="builder"), last),
    ], False


def seed(home: Path, count: int) -> int:
    projects = ProjectStore.open(home / ".cuttlefish" / "projects.db")
    now = datetime.now(UTC)
    try:
        for index in range(count):
            state, offset = _state_for(index, count)
            name = f"{PREFIX}{index + 1:03d}-{_WORDS[index % len(_WORDS)]}"
            root = home / "seed-projects" / name
            root.mkdir(parents=True, exist_ok=True)
            project = projects.register(name=name, root=str(root), roles=template_roles())
            if state == "idle":
                continue
            team_id = f"team-{project.id[:8]}"
            events, review = _journal(state, offset, root)
            store = EpisodicStore.open(root / ".cuttlefish" / "episodic.db")
            try:
                for payload, minutes_ago in events:
                    store.append(team_id, payload, ts=now - timedelta(minutes=minutes_ago))
            finally:
                store.close()
            projects.record_team_started(project.id, team_id, require_approval=review)
    finally:
        projects.close()
    return count


def clear(home: Path) -> int:
    projects = ProjectStore.open(home / ".cuttlefish" / "projects.db")
    try:
        seeded = [p for p in projects.list() if p.name.startswith(PREFIX)]
        for project in seeded:
            projects.deregister(project.id)
    finally:
        projects.close()
    shutil.rmtree(home / "seed-projects", ignore_errors=True)
    return len(seeded)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("seed", "clear"))
    parser.add_argument("--home", required=True, type=Path, help="a brand-new scratch HOME")
    parser.add_argument("--count", type=int, default=100)
    args = parser.parse_args(argv)
    home = args.home.resolve()
    if home == Path(pwd.getpwuid(os.getuid()).pw_dir).resolve():
        print("seed_fleet: --home is your real home directory; use a scratch one", file=sys.stderr)
        return 2
    if args.command == "seed":
        print(f"seeded {seed(home, args.count)} projects under {home}")
    else:
        print(f"cleared {clear(home)} seeded projects under {home}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
