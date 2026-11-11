"""Per-project schedules: a goal the builder is asked on a recurrence (CUT-244).

Stored in the same ``projects.db`` as the registry, in their own table. This module only
stores schedules and works out which are due; the daemon loop that starts a goal is separate.

A recurrence is one of these spellings, **always read in UTC** (so a daylight-saving change
never moves or repeats a run; ``daily 09:30`` is 09:30 UTC all year):

- ``every <N>m`` or ``every <N>h`` (N a positive whole number, at most 30 days): N minutes or
  hours after the last run, or after the schedule was created if it has not run yet.
- ``daily HH:MM``: the next HH:MM UTC after the last run (or creation).
- ``weekdays HH:MM``: Monday to Friday at HH:MM; ``weekly <mon..sun> HH:MM``: one day a week.
- ``cron <minute> <hour> <day-of-month> <month> <day-of-week>``: the usual five fields with
  ``*``, ``N``, ``A-B``, ``A,B`` and a ``/step`` (``*/15``, ``10-50/10``); Sunday is 0 or 7. When
  both day fields are restricted either may match, as in cron. No names, no ``@daily``.

A schedule that was due while the daemon was down is due once, not once per missed slot:
:meth:`ScheduleStore.record_run` moves ``next_due`` on from the time it ran, not from the slot.
A schedule may carry a **grace** (minutes): a run that is later than that when the daemon gets to
it is skipped, not run (a goal for "this morning" is not wanted at night). It may also carry a
**precheck**, a command the daemon runs first; the run is skipped unless it exits 0
(``fleet/precheck.py``). Neither changes when a schedule is next due.
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
_TIME = r"(\d{2}):(\d{2})"
_WEEKDAYS = re.compile(rf"weekdays {_TIME}")
_DAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
_WEEKLY = re.compile(rf"weekly ({'|'.join(_DAY_NAMES)}) {_TIME}")
_CRON_PREFIX = "cron "
#: How far ahead a cron expression is searched before it is called impossible (``0 0 31 2 *``).
_CRON_HORIZON_DAYS = 366 * 5
#: The longest missed-run grace: a week. A longer one is not a grace.
MAX_GRACE_MINUTES = 7 * 24 * 60
MAX_PRECHECK_CHARS = 500

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    goal TEXT NOT NULL,
    recurrence TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run TEXT,
    last_result TEXT,
    next_due TEXT NOT NULL,
    grace_minutes INTEGER,
    precheck TEXT
)
"""
#: Tables made before CUT-297 lack these two columns.
_ADD_COLUMNS = (
    "ALTER TABLE schedules ADD COLUMN grace_minutes INTEGER",
    "ALTER TABLE schedules ADD COLUMN precheck TEXT",
)


class RecurrenceError(ValueError):
    """The recurrence text is not one of the documented spellings."""


class ScheduleNotFoundError(LookupError):
    pass


def _cron_field(text: str, low: int, high: int) -> frozenset[int]:
    """One cron field as the set of values it matches; raises on anything outside `low`..`high`."""
    values: set[int] = set()
    for part in text.split(","):
        body, sep, step_text = part.partition("/")
        if sep and not (step_text.isdigit() and int(step_text) > 0):
            raise RecurrenceError(f"bad step in {text!r}")
        step = int(step_text) if sep else 1
        if body == "*":
            first, last = low, high
        elif m := re.fullmatch(r"(\d+)-(\d+)", body):
            first, last = int(m[1]), int(m[2])
        elif body.isdigit():
            first = int(body)
            last = high if sep else first
        else:
            raise RecurrenceError(f"unusable cron field {text!r}")
        if not (low <= first <= last <= high):
            raise RecurrenceError(f"cron value out of range in {text!r} (allowed {low}-{high})")
        values.update(range(first, last + 1, step))
    return frozenset(values)


@dataclass(frozen=True, slots=True)
class _Cron:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 is Sunday
    any_day: bool
    any_weekday: bool

    def day_matches(self, day: datetime) -> bool:
        if day.month not in self.months:
            return False
        in_month = day.day in self.days
        in_week = (day.weekday() + 1) % 7 in self.weekdays
        if self.any_day and self.any_weekday:
            return True
        if self.any_day:
            return in_week
        if self.any_weekday:
            return in_month
        return in_month or in_week  # both restricted: either, as cron does


def _parse_cron(fields: list[str]) -> _Cron:
    if len(fields) != 5:
        raise RecurrenceError("cron needs five fields: minute hour day-of-month month day-of-week")
    minutes, hours, days, months, weekdays = (
        _cron_field(f, lo, hi)
        for f, (lo, hi) in zip(fields, ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7)), strict=True)
    )
    return _Cron(
        minutes, hours, days, months,
        frozenset(d % 7 for d in weekdays),
        any_day=fields[2] == "*", any_weekday=fields[4] == "*",
    )  # fmt: skip


def _time_parts(hour: str, minute: str) -> tuple[int, int]:
    h, m = int(hour), int(minute)
    if h > 23 or m > 59:
        raise RecurrenceError(f"{hour}:{minute} is not a time of day")
    return h, m


def _next_cron(cron: _Cron, after: datetime) -> datetime:
    """The first minute strictly after `after` that matches, searching day by day."""
    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for offset in range(_CRON_HORIZON_DAYS):
        day = (start + timedelta(days=offset)).replace(hour=0, minute=0)
        if not cron.day_matches(day):
            continue
        for hour in sorted(cron.hours):
            for minute in sorted(cron.minutes):
                slot = day.replace(hour=hour, minute=minute)
                if slot >= start:
                    return slot
    raise RecurrenceError("that cron expression never matches a day")


def next_after(recurrence: str, after: datetime) -> datetime:
    """The first run time strictly after `after` (a timezone-aware datetime, read as UTC)."""
    after = after.astimezone(UTC)
    if (m := _EVERY.fullmatch(recurrence)) and int(m[1]) > 0:
        n = int(m[1])
        if n > _MAX_EVERY.total_seconds() / (60 if m[2] == "m" else 3600):
            raise RecurrenceError(f"{recurrence!r} is longer than 30 days; use a shorter gap")
        return after + (timedelta(minutes=n) if m[2] == "m" else timedelta(hours=n))
    if m := _DAILY.fullmatch(recurrence):
        hour, minute = _time_parts(m[1], m[2])
        slot = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return slot if slot > after else slot + timedelta(days=1)
    if m := _WEEKDAYS.fullmatch(recurrence):
        hour, minute = _time_parts(m[1], m[2])
        return _next_cron(_parse_cron([str(minute), str(hour), "*", "*", "1-5"]), after)
    if m := _WEEKLY.fullmatch(recurrence):
        hour, minute = _time_parts(m[2], m[3])
        return _next_cron(
            _parse_cron([str(minute), str(hour), "*", "*", str(_DAY_NAMES.index(m[1]))]), after
        )
    if recurrence.startswith(_CRON_PREFIX):
        return _next_cron(_parse_cron(recurrence[len(_CRON_PREFIX) :].split()), after)
    raise RecurrenceError(
        f"unknown recurrence {recurrence!r}: use 'every <N>m', 'every <N>h', 'daily HH:MM', "
        "'weekdays HH:MM', 'weekly <mon..sun> HH:MM' or 'cron <5 fields>' (UTC)"
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
    #: Minutes past ``next_due`` after which a run is skipped; ``None`` runs however late.
    grace_minutes: int | None = None
    #: A command that must exit 0 for the run to go ahead; ``None`` has no check.
    precheck: str | None = None

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


def late_by_minutes(schedule: Schedule, now: datetime) -> int | None:
    """Minutes past its slot a due run is, when that is more than its grace; else ``None``."""
    if schedule.grace_minutes is None:
        return None
    late = int((now - schedule.next_due).total_seconds() // 60)
    return late if late > schedule.grace_minutes else None


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
        grace_minutes=row["grace_minutes"],
        precheck=row["precheck"],
    )


class ScheduleStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_CREATE_TABLE)
        have = {r["name"] for r in self._conn.execute("PRAGMA table_info(schedules)")}
        for statement in _ADD_COLUMNS:
            if statement.split()[-2] not in have:
                self._conn.execute(statement)
        self._conn.commit()

    @classmethod
    def open(cls, path: Path | None = None) -> ScheduleStore:
        """Open the schedules table in `path` (default: the projects registry file)."""
        resolved = path if path is not None else default_projects_db()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return cls(sqlite3.connect(resolved, check_same_thread=False))

    def close(self) -> None:
        self._conn.close()

    def add(
        self,
        project_id: str,
        goal: str,
        recurrence: str,
        *,
        now: datetime,
        grace_minutes: int | None = None,
        precheck: str | None = None,
    ) -> Schedule:
        if not goal.strip():
            raise ValueError("a schedule needs a goal")
        if grace_minutes is not None and not 1 <= grace_minutes <= MAX_GRACE_MINUTES:
            raise ValueError(f"the grace is 1 to {MAX_GRACE_MINUTES} minutes")
        precheck = (precheck or "").strip() or None
        if precheck is not None and (len(precheck) > MAX_PRECHECK_CHARS or "\n" in precheck):
            raise ValueError(f"a precheck is one line of at most {MAX_PRECHECK_CHARS} characters")
        schedule = Schedule(
            id=uuid.uuid4().hex[:12],
            project_id=project_id,
            goal=goal.strip(),
            recurrence=recurrence,
            enabled=True,
            next_due=next_after(recurrence, now),  # raises on a bad recurrence
            grace_minutes=grace_minutes,
            precheck=precheck,
        )
        self._conn.execute(
            "INSERT INTO schedules (id, project_id, goal, recurrence, enabled, next_due,"
            " grace_minutes, precheck) VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
            (
                schedule.id, project_id, schedule.goal, recurrence,
                schedule.next_due.isoformat(), grace_minutes, precheck,
            ),
        )  # fmt: skip
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
