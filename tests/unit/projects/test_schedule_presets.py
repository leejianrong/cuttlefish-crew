"""Weekday, weekly and cron recurrences, the missed-run grace and the precheck field (CUT-297).

Every time here is UTC: the module never reads a local zone (ADR-0051)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cuttlefish.projects.schedules import (
    MAX_GRACE_MINUTES,
    RecurrenceError,
    ScheduleStore,
    late_by_minutes,
    next_after,
)

FRI = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)  # a Friday


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def test_weekdays_skips_the_weekend() -> None:
    assert next_after("weekdays 09:00", FRI) == at(16, 9)  # Friday 10:00 -> Monday
    assert next_after("weekdays 11:00", FRI) == at(13, 11)  # later the same Friday
    assert next_after("weekdays 09:00", at(14, 9)) == at(16, 9)  # a Saturday


def test_weekly_names_one_day() -> None:
    assert next_after("weekly mon 09:30", FRI) == at(16, 9, 30)
    assert next_after("weekly fri 10:00", FRI) == at(20, 10)  # strictly after
    assert next_after("weekly sun 00:05", FRI) == at(15, 0, 5)


@pytest.mark.parametrize(
    ("cron", "after", "expected"),
    [
        ("*/15 * * * *", at(13, 10, 7), at(13, 10, 15)),
        ("0 9 * * 1-5", FRI, at(16, 9)),
        ("30 8,17 * * *", at(13, 9), at(13, 17, 30)),
        ("0 0 1 * *", FRI, datetime(2026, 12, 1, 0, 0, tzinfo=UTC)),
        ("0 12 * * 0", FRI, at(15, 12)),  # Sunday as 0
        ("0 12 * * 7", FRI, at(15, 12)),  # and as 7
        ("10-20/5 4 * * *", at(13, 4, 12), at(13, 4, 15)),
        # day-of-month and day-of-week both given: either matches, as cron does
        ("0 6 14 * 1", at(13, 7), at(14, 6)),
        ("0 6 14 * 1", at(14, 7), at(16, 6)),
    ],
)
def test_cron_five_fields(cron: str, after: datetime, expected: datetime) -> None:
    assert next_after(f"cron {cron}", after) == expected


def test_cron_is_read_in_utc_even_for_a_clock_in_another_zone() -> None:
    from datetime import timezone

    other = datetime(2026, 11, 13, 5, 0, tzinfo=timezone(timedelta(hours=-5)))  # 10:00 UTC
    assert next_after("cron 0 11 * * *", other) == at(13, 11)


@pytest.mark.parametrize(
    "bad",
    [
        "cron",
        "cron * * * *",
        "cron 60 * * * *",
        "cron * 24 * * *",
        "cron * * 32 * *",
        "cron * * * 13 *",
        "cron * * * * 8",
        "cron */0 * * * *",
        "cron 5-1 * * * *",
        "cron 0 0 31 2 *",  # a day that never comes
        "weekdays 24:00",
        "weekdays",
        "weekly funday 09:00",
        "weekly mon",
    ],
)
def test_unusable_recurrences_are_refused(bad: str) -> None:
    with pytest.raises(RecurrenceError):
        next_after(bad, FRI)


def test_the_error_lists_the_spellings() -> None:
    with pytest.raises(RecurrenceError, match=r"weekdays HH:MM.*cron"):
        next_after("hourly", FRI)


def test_grace_and_precheck_round_trip_and_default_to_off(tmp_path: Path) -> None:
    store = ScheduleStore.open(tmp_path / "p.db")
    plain = store.add("p", "g", "daily 06:00", now=FRI)
    assert plain.grace_minutes is None and plain.precheck is None
    full = store.add(
        "p",
        "g",
        "weekdays 06:00",
        now=FRI,
        grace_minutes=30,
        precheck="git diff --quiet origin/main",
    )
    got = store.get(full.id)
    assert got.grace_minutes == 30 and got.precheck == "git diff --quiet origin/main"
    assert got.to_json()["grace_minutes"] == 30 and got.to_json()["precheck"] == got.precheck


@pytest.mark.parametrize("grace", [0, -1, MAX_GRACE_MINUTES + 1])
def test_grace_must_be_a_sensible_number_of_minutes(tmp_path: Path, grace: int) -> None:
    store = ScheduleStore.open(tmp_path / "p.db")
    with pytest.raises(ValueError, match="grace"):
        store.add("p", "g", "daily 06:00", now=FRI, grace_minutes=grace)


@pytest.mark.parametrize("command", ["x" * 501, "echo a\necho b"])
def test_precheck_is_one_short_line(tmp_path: Path, command: str) -> None:
    store = ScheduleStore.open(tmp_path / "p.db")
    with pytest.raises(ValueError, match="precheck"):
        store.add("p", "g", "daily 06:00", now=FRI, precheck=command)


def test_a_blank_precheck_means_none(tmp_path: Path) -> None:
    store = ScheduleStore.open(tmp_path / "p.db")
    assert store.add("p", "g", "daily 06:00", now=FRI, precheck="  ").precheck is None


def test_an_older_table_gains_the_new_columns(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE schedules (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, goal TEXT NOT NULL,"
        " recurrence TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, last_run TEXT,"
        " last_result TEXT, next_due TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO schedules (id, project_id, goal, recurrence, next_due) VALUES"
        " ('a', 'p', 'g', 'daily 06:00', ?)",
        (FRI.isoformat(),),
    )
    conn.commit()
    conn.close()
    store = ScheduleStore.open(path)
    assert store.get("a").grace_minutes is None and store.get("a").precheck is None


def test_late_by_minutes_only_counts_a_schedule_with_a_grace(tmp_path: Path) -> None:
    store = ScheduleStore.open(tmp_path / "p.db")
    plain = store.add("p", "g", "every 1h", now=FRI)
    graced = store.add("p", "g", "every 1h", now=FRI, grace_minutes=30)
    now = FRI + timedelta(hours=1, minutes=45)
    assert late_by_minutes(plain, now) is None
    assert late_by_minutes(graced, now) == 45
    assert late_by_minutes(graced, FRI + timedelta(hours=1, minutes=30)) is None  # inside the grace
