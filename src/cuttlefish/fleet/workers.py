"""Which worker machines are reachable right now (ADR-0057).

A worker is paired and kept like a device (`devices.py`, in `workers.db`). Whether it is *online* is
not stored: it is whether it sent a heartbeat lately, so a machine that lost its network, or was
shut, shows as offline after `OFFLINE_AFTER_SECONDS` with nothing to clean up, and a controller that
restarts starts with every worker offline until each one calls back in.

A heartbeat also carries what the machine says about itself: `host`, `platform` and `health` (load,
cpus, disk, agents running; CUT-208). `health` is a closed set of numbers the controller checks and
keeps, so a worker cannot put free text on the dashboard. Other things a heartbeat may grow (the
per-backend usage CUT-292 wants) are sibling keys read the same way: named, typed, bounded.
Low capacity is flagged for a person to see and never stops a placement or a start.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cuttlefish.fleet.devices import Device
from cuttlefish.usage import BACKENDS, Window

#: A worker beats this often (`cuttlefish.worker.run`)...
HEARTBEAT_EVERY_SECONDS = 15.0
#: ...and is offline once it has been silent for this long (three beats).
OFFLINE_AFTER_SECONDS = 45.0
#: What a worker may say about itself, kept short and printable.
_MAX_FIELD = 80


#: Flagged when the disk has less than this free, or less than this fraction of it.
DISK_LOW_BYTES = 2 * 1024**3
DISK_LOW_FRACTION = 0.10
#: Flagged when the one-minute load is above this many times the cpu count.
LOAD_HIGH_PER_CPU = 1.5
#: The numbers a heartbeat's `health` may hold, each bounded; anything else is dropped.
_HEALTH_FIELDS = ("load1", "cpus", "disk_free", "disk_total", "agents")
_MAX_NUMBER = 1e15


@dataclass(frozen=True, slots=True)
class Beat:
    at: float
    host: str
    platform: str
    health: dict[str, float] | None = None
    #: What each backend on the machine reported about its limits, checked (CUT-292).
    usage: dict[str, dict[str, Any]] | None = None


def _clean(raw: object) -> str:
    text = raw if isinstance(raw, str) else ""
    return "".join(ch for ch in text if ch.isprintable()).strip()[:_MAX_FIELD]


def _clean_health(raw: object) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    found: dict[str, float] = {}
    for name in _HEALTH_FIELDS:
        value = raw.get(name)
        if (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and 0 <= value < _MAX_NUMBER
        ):
            found[name] = value
    return found or None


_MAX_WINDOWS = 6
_REPORTING = frozenset(name for name, reports in BACKENDS if reports)


def _clean_usage(raw: object) -> dict[str, dict[str, Any]] | None:
    """`backend -> {at, windows}` from a heartbeat's `usage`: named backends only, a few windows
    each, numbers bounded, names printable and short. The warning is worked out here, from the
    numbers, never taken from the worker."""
    if not isinstance(raw, dict):
        return None
    found: dict[str, dict[str, Any]] = {}
    for backend in _REPORTING:
        entry = raw.get(backend)
        listed = entry.get("windows") if isinstance(entry, dict) else None
        if not isinstance(listed, list):
            continue
        windows: list[dict[str, Any]] = []
        for item in listed[:_MAX_WINDOWS]:
            if not isinstance(item, dict):
                continue
            percent = item.get("used_percent")
            resets, minutes = item.get("resets_at"), item.get("minutes")
            status = item.get("status")
            window = Window(
                _clean(item.get("name"))[:24] or "window",
                _within(percent, 1000),
                _whole(resets, 1e11),
                _whole(minutes, 1e7),
                status if status in ("allowed", "allowed_warning", "rejected") else None,
            )
            windows.append(window.as_json())
        at = entry.get("at") if isinstance(entry, dict) else None
        if windows:
            found[backend] = {
                "reported": True,
                "at": _within(at, 1e11),
                "windows": windows,
                "warn": any(w["warn"] for w in windows),
            }
    return found or None


def _within(value: object, limit: float) -> float | None:
    """`value` as a number if it is one and is not negative or absurd, else `None`."""
    if isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value < limit:
        return float(value)
    return None


def _whole(value: object, limit: float) -> int | None:
    number = _within(value, limit)
    return None if number is None else int(number)


def flags(health: dict[str, float] | None) -> list[str]:
    """What a person should know about a machine's capacity: `disk` (nearly full), `load` (busy)."""
    found: list[str] = []
    if health is None:
        return found
    free, total = health.get("disk_free"), health.get("disk_total")
    if free is not None and (free < DISK_LOW_BYTES or (total and free < total * DISK_LOW_FRACTION)):
        found.append("disk")
    load, cpus = health.get("load1"), health.get("cpus")
    if load is not None and cpus and load > cpus * LOAD_HIGH_PER_CPU:
        found.append("load")
    return found


class WorkerLiveness:
    def __init__(
        self,
        *,
        offline_after: float = OFFLINE_AFTER_SECONDS,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._offline_after = offline_after
        self._now = now
        self._beats: dict[str, Beat] = {}

    def beat(self, worker_id: str, info: dict[str, object] | None = None) -> None:
        previous = self._beats.get(worker_id)
        if info is None:  # a sign of life that says nothing new about the machine
            host, kind, health = (
                (previous.host, previous.platform, previous.health) if previous else ("", "", None)
            )
            usage = previous.usage if previous else None
        else:
            host, kind = _clean(info.get("host")), _clean(info.get("platform"))
            health = _clean_health(info.get("health"))
            usage = _clean_usage(info.get("usage"))
        self._beats[worker_id] = Beat(self._now(), host, kind, health, usage)

    def forget(self, worker_id: str) -> None:
        self._beats.pop(worker_id, None)

    def online(self, worker_id: str) -> bool:
        beat = self._beats.get(worker_id)
        return beat is not None and self._now() - beat.at < self._offline_after

    def as_json(self, worker: Device) -> dict[str, object]:
        beat = self._beats.get(worker.id)
        health = beat.health if beat and self.online(worker.id) else None
        return {
            **worker.as_json(),
            "online": self.online(worker.id),
            "host": beat.host if beat else "",
            "platform": beat.platform if beat else "",
            "health": health,
            "flags": flags(health),
            "usage": beat.usage if beat and self.online(worker.id) else None,
        }
