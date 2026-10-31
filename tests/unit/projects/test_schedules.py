from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from cuttlefish.projects.schedules import (
    RecurrenceError,
    ScheduleNotFoundError,
    ScheduleStore,
    next_after,
)

T0 = datetime(2026, 3, 8, 12, 0, tzinfo=UTC)


def test_every_minutes_and_hours() -> None:
    assert next_after("every 30m", T0) == T0 + timedelta(minutes=30)
    assert next_after("every 2h", T0) == T0 + timedelta(hours=2)


def test_daily_is_the_next_slot_strictly_after() -> None:
    assert next_after("daily 13:15", T0) == datetime(2026, 3, 8, 13, 15, tzinfo=UTC)
    assert next_after("daily 12:00", T0) == datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
    assert next_after("daily 09:00", T0) == datetime(2026, 3, 9, 9, 0, tzinfo=UTC)


def test_daily_is_utc_whatever_zone_the_clock_is_in() -> None:
    # 2026-03-08 is a US daylight-saving change. An aware time in another zone is converted
    # to UTC first, and the slot is 09:00 UTC on both sides of the change.
    other = datetime(2026, 3, 8, 3, 0, tzinfo=timezone(timedelta(hours=-5)))  # 08:00 UTC
    assert next_after("daily 09:00", other) == datetime(2026, 3, 8, 9, 0, tzinfo=UTC)
    day = datetime(2026, 3, 9, 9, 0, tzinfo=UTC)
    assert next_after("daily 09:00", day) == day + timedelta(days=1)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "every 0m",
        "every 5d",
        "every m",
        "daily 24:00",
        "daily 9:00",
        "daily 12:60",
        "* * * * *",
    ],
)
def test_bad_recurrence(bad: str) -> None:
    with pytest.raises(RecurrenceError):
        next_after(bad, T0)


def test_due_only_enabled_and_passed_oldest_first(tmp_path: Path) -> None:
    store = ScheduleStore.open(tmp_path / "p.db")
    a = store.add("p1", "a", "every 2h", now=T0)
    b = store.add("p1", "b", "every 1h", now=T0)
    c = store.add("p2", "c", "every 1h", now=T0)
    store.set_enabled(c.id, False, now=T0)
    assert store.due(T0 + timedelta(minutes=59)) == []
    assert [s.id for s in store.due(T0 + timedelta(hours=3))] == [b.id, a.id]


def test_a_missed_run_is_due_once_and_next_due_moves_from_the_run(tmp_path: Path) -> None:
    store = ScheduleStore.open(tmp_path / "p.db")
    s = store.add("p1", "g", "every 1h", now=T0)
    late = T0 + timedelta(hours=10)
    assert [x.id for x in store.due(late)] == [s.id]
    ran = store.record_run(s.id, "started", now=late)
    assert ran.next_due == late + timedelta(hours=1)
    assert store.due(late) == []


def test_enabling_counts_from_now(tmp_path: Path) -> None:
    store = ScheduleStore.open(tmp_path / "p.db")
    s = store.add("p1", "g", "every 1h", now=T0)
    store.set_enabled(s.id, False, now=T0)
    week = T0 + timedelta(days=7)
    assert store.due(week) == []
    store.set_enabled(s.id, True, now=week)
    assert store.due(week) == []
    assert len(store.due(week + timedelta(hours=1))) == 1


def test_persists_across_reopen_and_remove(tmp_path: Path) -> None:
    path = tmp_path / "p.db"
    store = ScheduleStore.open(path)
    s = store.add("p1", "  do the thing ", "daily 06:00", now=T0)
    store.record_run(s.id, "skipped: busy", now=T0)
    store.close()
    again = ScheduleStore.open(path)
    got = again.get(s.id)
    assert got.goal == "do the thing"
    assert (got.recurrence, got.enabled, got.last_result) == ("daily 06:00", True, "skipped: busy")
    assert got.last_run == T0
    assert again.entries("p2") == [] and len(again.entries("p1")) == 1
    assert again.remove(s.id) and not again.remove(s.id)
    with pytest.raises(ScheduleNotFoundError):
        again.get(s.id)


def test_add_rejects_bad_input(tmp_path: Path) -> None:
    store = ScheduleStore.open(tmp_path / "p.db")
    with pytest.raises(RecurrenceError):
        store.add("p1", "g", "weekly", now=T0)
    with pytest.raises(ValueError):
        store.add("p1", "  ", "every 1h", now=T0)
    assert store.entries() == []
