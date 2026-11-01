"""Per-project schedules: a goal the builder is asked on a recurrence (CUT-244).

Stored in the same ``projects.db`` as the registry, in their own table. This module only
stores schedules and works out which are due; the daemon loop that starts a goal is separate.

A recurrence is one of two spellings, **always read in UTC** (so a daylight-saving change
never moves or repeats a run; ``daily 09:30`` is 09:30 UTC all year):

- ``every <N>m`` or ``every <N>h`` (N a positive whole number, at most 30 days): N minutes or
  hours after the last run, or after the schedule was created if it has not run yet.
- ``daily HH:MM``: the next HH:MM UTC after the last run (or creation).

A schedule that was due while the daemon was down is due once, not once per missed slot:
:meth:`ScheduleStore.record_run` moves ``next_due`` on from the time it ran, not from the slot.
"""

from __future__ import annotations

import dataclasses
import re
import sqlite3
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cuttlefish.projects.store import default_projects_db

_EVERY = re.compile(r"every (\d+)([mh])")
#: The longest "every" gap: a month. Longer is a typo (``every 99999h`` is a run in 2038).
_MAX_EVERY = timedelta(days=30)
_DAILY = re.compile(r"daily (\d{2}):(\d{2})")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    goal TEXT NOT NULL,
    recurrence TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run TEXT,
    last_result TEXT,
    next_due TEXT NOT NULL
)
"""


class RecurrenceError(ValueError):
    """The recurrence text is not one of the documented spellings."""


class ScheduleNotFoundError(LookupError):
    pass


def next_after(recurrence: str, after: datetime) -> datetime:
    """The first run time strictly after `after` (a timezone-aware datetime, read as UTC)."""
    after = after.astimezone(UTC)
    if (m := _EVERY.fullmatch(recurrence)) and int(m[1]) > 0:
        n = int(m[1])
        if n > _MAX_EVERY.total_seconds() / (60 if m[2] == "m" else 3600):
            raise RecurrenceError(f"{recurrence!r} is longer than 30 days; use a shorter gap")
        return after + (timedelta(minutes=n) if m[2] == "m" else timedelta(hours=n))
    if (m := _DAILY.fullmatch(recurrence)) and int(m[1]) < 24 and int(m[2]) < 60:
        slot = after.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
        return slot if slot > after else slot + timedelta(days=1)
    raise RecurrenceError(
        f"unknown recurrence {recurrence!r}: use 'every <N>m', 'every <N>h' or 'daily HH:MM' (UTC)"
    )


@dataclass(frozen=True, slots=True)
class Schedule:
    id: str
    project_id: str
    goal: str
    recurrence: str
    enabled: bool
    next_due: datetime
    last_run: datetime | None = None
    last_result: str | None = None

    def to_json(self) -> dict[str, Any]:
        """Times as ISO 8601 UTC, for the API and the CLI."""
        return {
            **dataclasses.asdict(self),
            "next_due": self.next_due.isoformat(),
            "last_run": self.last_run.isoformat() if self.last_run else None,
        }


def due(schedules: list[Schedule], now: datetime) -> list[Schedule]:
    """The enabled schedules whose ``next_due`` has passed, oldest first."""
    ready = (s for s in schedules if s.enabled and s.next_due <= now)
    return sorted(ready, key=lambda s: s.next_due)


def _row(row: sqlite3.Row) -> Schedule:
    return Schedule(
        id=row["id"],
        project_id=row["project_id"],
        goal=row["goal"],
        recurrence=row["recurrence"],
        enabled=bool(row["enabled"]),
        next_due=datetime.fromisoformat(row["next_due"]),
        last_run=datetime.fromisoformat(row["last_run"]) if row["last_run"] else None,
        last_result=row["last_result"],
    )


class ScheduleStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()

    @classmethod
    def open(cls, path: Path | None = None) -> ScheduleStore:
        """Open the schedules table in `path` (default: the projects registry file)."""
        resolved = path if path is not None else default_projects_db()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return cls(sqlite3.connect(resolved, check_same_thread=False))

    def close(self) -> None:
        self._conn.close()

    def add(self, project_id: str, goal: str, recurrence: str, *, now: datetime) -> Schedule:
        if not goal.strip():
            raise ValueError("a schedule needs a goal")
        schedule = Schedule(
            id=uuid.uuid4().hex[:12],
            project_id=project_id,
            goal=goal.strip(),
            recurrence=recurrence,
            enabled=True,
            next_due=next_after(recurrence, now),  # raises on a bad recurrence
        )
        self._conn.execute(
            "INSERT INTO schedules (id, project_id, goal, recurrence, enabled, next_due) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (schedule.id, project_id, schedule.goal, recurrence, schedule.next_due.isoformat()),
        )
        self._conn.commit()
        return schedule

    def get(self, schedule_id: str) -> Schedule:
        row = self._conn.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
        if row is None:
            raise ScheduleNotFoundError(schedule_id)
        return _row(row)

    def entries(self, project_id: str | None = None) -> list[Schedule]:
        rows = self._conn.execute(
            "SELECT * FROM schedules WHERE (? IS NULL OR project_id = ?) ORDER BY next_due",
            (project_id, project_id),
        )
        return [_row(r) for r in rows]

    def due(self, now: datetime) -> list[Schedule]:
        return due(self.entries(), now)

    def set_enabled(self, schedule_id: str, enabled: bool, *, now: datetime) -> Schedule:
        """Enabling counts from `now`, so a schedule off for a week does not fire at once."""
        current = self.get(schedule_id)
        next_due = next_after(current.recurrence, now) if enabled else current.next_due
        self._conn.execute(
            "UPDATE schedules SET enabled = ?, next_due = ? WHERE id = ?",
            (int(enabled), next_due.isoformat(), schedule_id),
        )
        self._conn.commit()
        return replace(current, enabled=enabled, next_due=next_due)

    def record_run(self, schedule_id: str, result: str, *, now: datetime) -> Schedule:
        """Note a run (or a skip) at `now` and move `next_due` on from `now`."""
        current = self.get(schedule_id)
        next_due = next_after(current.recurrence, now)
        self._conn.execute(
            "UPDATE schedules SET last_run = ?, last_result = ?, next_due = ? WHERE id = ?",
            (now.isoformat(), result, next_due.isoformat(), schedule_id),
        )
        self._conn.commit()
        return replace(current, last_run=now, last_result=result, next_due=next_due)

    def remove(self, schedule_id: str) -> bool:
        cursor = self._conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        self._conn.commit()
        return cursor.rowcount > 0
