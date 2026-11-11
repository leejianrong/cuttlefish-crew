"""What each agent backend says about its own usage limits, and when they reset (CUT-292).

Read-only, and only what a backend already tells us over its existing headless surface; no new
protocol and no number of our own:

- **Claude Code** sends `rate_limit_event` in its stream-json output (`rate_limit_info`: a `status`
  of allowed, allowed_warning or rejected, `resetsAt` in epoch seconds, `rateLimitType` and
  `utilization`, and per-window `unifiedWindows`).
- **Codex** sends `account/rateLimits/updated` over `app-server` (sparse snapshots with a `primary`
  and a `secondary` window: `usedPercent`, `windowDurationMins`, `resetsAt`).
- **kopicode** reports no usage window, only a failed round when a provider answers 429. It is
  listed as not reported.

A backend that has said nothing is "not reported", never a made-up zero. Nothing here is a
credential and nothing is sent anywhere: the daemon shows it, and a worker adds it to its
heartbeat (`fleet/workers.py` cleans it). Account switching and cookie entry are not built
(ADR-0006, ADR-0031).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

#: A window at or over this share of its limit is a warning.
WARN_AT_PERCENT = 80.0

#: The backends cuttlefish can ask for usage, in the order shown, and whether each reports any.
BACKENDS: tuple[tuple[str, bool], ...] = (
    ("claude-code", True),
    ("codex", True),
    ("kopicode", False),
)

_WINDOW_LABELS = {
    "five_hour": "5 hours",
    "seven_day": "7 days",
    "seven_day_opus": "7 days (Opus)",
    "seven_day_sonnet": "7 days (Sonnet)",
    "seven_day_overage_included": "7 days (with overage)",
    "overage": "Overage",
    "primary": "Primary",
    "secondary": "Secondary",
}


@dataclass(frozen=True, slots=True)
class Window:
    """One limit window: how much of it is used and when it resets."""

    name: str
    used_percent: float | None
    resets_at: int | None = None
    minutes: int | None = None
    #: The backend's own word, when it gives one: allowed, allowed_warning or rejected.
    status: str | None = None

    @property
    def warn(self) -> bool:
        return (
            self.used_percent is not None and self.used_percent >= WARN_AT_PERCENT
        ) or self.status in ("allowed_warning", "rejected")

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": _WINDOW_LABELS.get(self.name, self.name),
            "used_percent": self.used_percent,
            "resets_at": self.resets_at,
            "minutes": self.minutes,
            "status": self.status,
            "warn": self.warn,
        }


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _epoch(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number > 0 else None


def _percent_from_utilization(value: object) -> float | None:
    """Claude Code's `utilization` as a percent. Its scale is not documented; the usage headers it
    mirrors give a fraction of one, so a value up to 1 is read as that and a larger one as already
    a percent."""
    number = _number(value)
    if number is None or number < 0:
        return None
    return round(number * 100 if number <= 1 else number, 1)


def claude_windows(event: Mapping[str, Any]) -> list[Window]:
    """The windows a Claude Code `rate_limit_event` describes (empty if it describes none)."""
    info = event.get("rate_limit_info")
    if not isinstance(info, Mapping):
        return []
    found: list[Window] = []
    unified = info.get("unifiedWindows")
    if isinstance(unified, Mapping):
        for name, window in unified.items():
            if isinstance(window, Mapping):
                status = window.get("status")
                found.append(
                    Window(
                        str(name),
                        _percent_from_utilization(window.get("utilization")),
                        _epoch(window.get("resetsAt")),
                        None,
                        status if isinstance(status, str) else None,
                    )
                )
    if not found:
        kind, status = info.get("rateLimitType"), info.get("status")
        percent = _percent_from_utilization(info.get("utilization"))
        if isinstance(kind, str) and (percent is not None or info.get("resetsAt")):
            found.append(
                Window(
                    kind,
                    percent,
                    _epoch(info.get("resetsAt")),
                    None,
                    status if isinstance(status, str) else None,
                )
            )
    return found


def codex_windows(params: Mapping[str, Any]) -> dict[str, Window]:
    """The windows a Codex `account/rateLimits/updated` (or `account/rateLimits/read`) carries,
    by name. A window the update leaves out or sends as null is not in the result."""
    snapshot = params.get("rateLimits")
    if not isinstance(snapshot, Mapping):
        return {}
    found: dict[str, Window] = {}
    for name in ("primary", "secondary"):
        window = snapshot.get(name)
        if isinstance(window, Mapping) and (used := _number(window.get("usedPercent"))) is not None:
            minutes = _epoch(window.get("windowDurationMins"))
            found[name] = Window(name, used, _epoch(window.get("resetsAt")), minutes)
    return found


class UsageBoard:
    """The latest windows each backend has reported in this process, and when."""

    def __init__(self, now: Callable[[], float] = time.time) -> None:
        self._now = now
        self._seen: dict[str, tuple[float, dict[str, Window]]] = {}

    def report(self, backend: str, windows: Mapping[str, Window]) -> None:
        """Merge what a backend just said: a window it sent replaces the old one, one it left out
        is kept (Codex sends sparse updates)."""
        if not windows:
            return
        kept = dict(self._seen[backend][1]) if backend in self._seen else {}
        kept.update(windows)
        self._seen[backend] = (self._now(), kept)

    def report_claude(self, event: Mapping[str, Any]) -> None:
        self.report("claude-code", {w.name: w for w in claude_windows(event)})

    def report_codex(self, params: Mapping[str, Any]) -> None:
        self.report("codex", codex_windows(params))

    def reset(self) -> None:
        self._seen.clear()

    def as_json(self) -> dict[str, dict[str, Any]]:
        """Every backend, reported or not: `reported: false` says nothing is known."""
        shown: dict[str, dict[str, Any]] = {}
        for backend, reports in BACKENDS:
            seen = self._seen.get(backend)
            if seen is None:
                shown[backend] = {
                    "reported": False,
                    "note": "Not reported yet." if reports else "Does not report usage.",
                }
                continue
            at, windows = seen
            listed = [w.as_json() for w in windows.values()]
            shown[backend] = {
                "reported": True,
                "at": at,
                "windows": listed,
                "warn": any(w["warn"] for w in listed),
            }
        return shown

    def heartbeat(self) -> dict[str, dict[str, Any]]:
        """What a worker adds to its heartbeat: only backends that have reported, compactly."""
        return {
            backend: {
                "at": at,
                "windows": [
                    {
                        "name": w.name,
                        "used_percent": w.used_percent,
                        "resets_at": w.resets_at,
                        "minutes": w.minutes,
                        "status": w.status,
                    }
                    for w in windows.values()
                ],
            }
            for backend, (at, windows) in self._seen.items()
        }


#: This process's board. Usage belongs to an account on a machine, not to a project, so unlike the
#: runtime it is one per process.
BOARD = UsageBoard()
