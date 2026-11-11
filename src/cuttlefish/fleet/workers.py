"""Which worker machines are reachable right now (ADR-0057).

A worker is paired and kept like a device (`devices.py`, in `workers.db`). Whether it is *online* is
not stored: it is whether it sent a heartbeat lately, so a machine that lost its network, or was
shut, shows as offline after `OFFLINE_AFTER_SECONDS` with nothing to clean up, and a controller that
restarts starts with every worker offline until each one calls back in.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from cuttlefish.fleet.devices import Device

#: A worker beats this often (`cuttlefish.worker.run`)...
HEARTBEAT_EVERY_SECONDS = 15.0
#: ...and is offline once it has been silent for this long (three beats).
OFFLINE_AFTER_SECONDS = 45.0
#: What a worker may say about itself, kept short and printable.
_MAX_FIELD = 80


@dataclass(frozen=True, slots=True)
class Beat:
    at: float
    host: str
    platform: str


def _clean(raw: object) -> str:
    text = raw if isinstance(raw, str) else ""
    return "".join(ch for ch in text if ch.isprintable()).strip()[:_MAX_FIELD]


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
        info = info or {}
        self._beats[worker_id] = Beat(
            self._now(), _clean(info.get("host")), _clean(info.get("platform"))
        )

    def forget(self, worker_id: str) -> None:
        self._beats.pop(worker_id, None)

    def online(self, worker_id: str) -> bool:
        beat = self._beats.get(worker_id)
        return beat is not None and self._now() - beat.at < self._offline_after

    def as_json(self, worker: Device) -> dict[str, object]:
        beat = self._beats.get(worker.id)
        return {
            **worker.as_json(),
            "online": self.online(worker.id),
            "host": beat.host if beat else "",
            "platform": beat.platform if beat else "",
        }
