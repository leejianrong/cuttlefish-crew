"""Scoring a long unattended Autopilot run (CUT-136, ADR-0044).

Nothing had run unattended for hours, so every claim about Autopilot was a guess. This module is the
scorer for the soak harness: it takes what a driver *saw* while a run went on, a list of timed
:class:`Sample` values and the :class:`Intervention` list, and says how the run went. It does no I/O
and reads no clock, so a saved run can be scored again and each rule is a unit test. The driver that
collects the samples from a daemon is :mod:`cuttlefish.soak_run`.

What a score means, so two runs can be compared:

* **Working** is time Autopilot was ``running`` with nothing waiting on a person. **Stalled** is the
  part of that where the builder's token count did not move for longer than ``stall_after_s``: it
  was running on paper and doing nothing. **Useful** is working minus stalled.
* **Blocked** is ``running`` with a request waiting in Needs you. **Stuck** is Autopilot's own
  ``stuck`` phase. Both are time a person was needed.
* An **intervention** is something a person (or the driver standing in for one) did. Approving the
  plan is expected, so it is counted apart from the rest; ``human`` counts the rest.
* Tokens are Autopilot's own figure (cache reads left out), a guard and not a bill. A cost per day
  is only given when the driver was told a price per million tokens.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
from collections.abc import Iterable, Mapping
from typing import Any

#: A run whose tokens stand still this long while running is counted as stalled from then on.
DEFAULT_STALL_AFTER_S = 600.0

#: Autopilot phases after which nothing happens without a person.
ENDED_PHASES = frozenset({"done", "stopped", "limit", "stuck"})

#: Interventions the harness expects of every run; they are not counted as a person stepping in.
EXPECTED_KINDS = frozenset({"approve_plan", "harness_stop"})


@dataclasses.dataclass(frozen=True)
class Sample:
    """What was seen at ``t`` seconds into the run."""

    t: float
    phase: str
    steps_done: int = 0
    steps_total: int = 0
    nudges: int = 0
    #: Autopilot's token figure for the run, or ``None`` when it could not be read.
    tokens: int | None = None
    #: Requests waiting in Needs you for this project.
    waiting: int = 0
    #: Commits and context refreshes since the run began (cumulative, so the last sample's count
    #: is the run's).
    commits: int = 0
    refreshes: int = 0

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Sample:
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


@dataclasses.dataclass(frozen=True)
class Intervention:
    """Something done to the run from outside: ``kind`` is one of ``approve_plan``, ``resume``,
    ``nudge_now``, ``message``, ``permission``, ``harness_stop``."""

    t: float
    kind: str
    note: str = ""

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Intervention:
        return cls(t=float(data["t"]), kind=str(data["kind"]), note=str(data.get("note", "")))


@dataclasses.dataclass(frozen=True)
class Score:
    arm: str
    wall_s: float
    planning_s: float
    awaiting_approval_s: float
    working_s: float
    stalled_s: float
    blocked_s: float
    paused_s: float
    stuck_s: float
    #: Time spent in a phase that ended the run before the driver stopped watching (``limit``,
    #: ``stopped`` and ``done`` after the first sample of that phase).
    ended_s: float
    steps_done: int
    steps_total: int
    nudges: int
    commits: int
    handoffs: int
    tokens: int | None
    tokens_per_day: int | None
    cost_per_day_usd: float | None
    expected: int
    human: int
    interventions: tuple[Intervention, ...]
    final_phase: str
    samples: int

    @property
    def useful_s(self) -> float:
        return max(0.0, self.working_s - self.stalled_s)

    @property
    def stuck_total_s(self) -> float:
        """Time a person was needed or the builder was going nowhere: stuck, blocked and stalled."""
        return self.stuck_s + self.blocked_s + self.stalled_s

    def to_json(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        data["interventions"] = [i.to_json() for i in self.interventions]
        data["useful_s"] = self.useful_s
        data["stuck_total_s"] = self.stuck_total_s
        return data


def score(
    samples: Iterable[Sample],
    interventions: Iterable[Intervention] = (),
    *,
    arm: str = "baseline",
    stall_after_s: float = DEFAULT_STALL_AFTER_S,
    usd_per_mtok: float | None = None,
) -> Score:
    """Score a run. Each interval between two samples is spent in the earlier sample's state."""
    seen = sorted(samples, key=lambda s: s.t)
    notes = tuple(sorted(interventions, key=lambda i: i.t))
    if not seen:
        raise ValueError("a run with no samples cannot be scored")

    buckets = dict.fromkeys(
        ("planning", "awaiting", "working", "blocked", "paused", "stuck", "ended"), 0.0
    )
    stalled = 0.0
    flat_since: float | None = None  # when the token count last changed, while working
    for i, (now, after) in enumerate(itertools.pairwise(seen)):
        bucket = _bucket(now)
        buckets[bucket] += after.t - now.t
        if bucket != "working" or now.tokens is None:
            flat_since = None
            continue
        if flat_since is None or seen[i - 1].tokens != now.tokens:
            flat_since = now.t
        if after.tokens == now.tokens:
            stalled += _past(flat_since, now.t, after.t, stall_after_s)

    first, last = seen[0], seen[-1]
    wall = last.t - first.t
    tokens = _tokens(seen)
    per_day = int(tokens * 86_400 / wall) if tokens is not None and wall > 0 else None
    cost = per_day / 1_000_000 * usd_per_mtok if per_day is not None and usd_per_mtok else None
    expected = sum(1 for i in notes if i.kind in EXPECTED_KINDS)
    return Score(
        arm=arm,
        wall_s=wall,
        planning_s=buckets["planning"],
        awaiting_approval_s=buckets["awaiting"],
        working_s=buckets["working"],
        stalled_s=min(stalled, buckets["working"]),
        blocked_s=buckets["blocked"],
        paused_s=buckets["paused"],
        stuck_s=buckets["stuck"],
        ended_s=buckets["ended"],
        steps_done=max(0, last.steps_done - first.steps_done),
        steps_total=last.steps_total,
        nudges=max(0, last.nudges - first.nudges),
        commits=last.commits,
        handoffs=last.refreshes,
        tokens=tokens,
        tokens_per_day=per_day,
        cost_per_day_usd=cost,
        expected=expected,
        human=len(notes) - expected,
        interventions=notes,
        final_phase=last.phase,
        samples=len(seen),
    )


def _bucket(sample: Sample) -> str:
    phase = sample.phase
    if phase == "running":
        return "blocked" if sample.waiting > 0 else "working"
    if phase in ("planning", "off"):
        return "planning"
    if phase == "awaiting_approval":
        return "awaiting"
    if phase == "paused":
        return "paused"
    if phase == "stuck":
        return "stuck"
    return "ended"


def _past(since: float, start: float, end: float, threshold: float) -> float:
    """How much of ``[start, end]`` lies more than ``threshold`` after ``since``."""
    return max(0.0, end - max(start, since + threshold))


def _tokens(seen: list[Sample]) -> int | None:
    known = [s.tokens for s in seen if s.tokens is not None]
    return max(known) - known[0] if known else None


# -- the report ----------------------------------------------------------------------------------


def _clock(seconds: float) -> str:
    minutes = round(seconds / 60)
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest:02d} min" if hours else f"{rest} min"


def _share(part: float, whole: float) -> str:
    return f"{part / whole:.0%}" if whole > 0 else "n/a"


def render_report(result: Score, meta: Mapping[str, Any] | None = None) -> str:
    """The score as a short markdown report, with the interventions listed."""
    meta = meta or {}
    wall = result.wall_s
    lines = [
        f"# Soak run: {meta.get('title', result.arm)}",
        "",
        f"- Arm: **{result.arm}**"
        + (" (no manager: Autopilot alone)" if result.arm == "baseline" else ""),
    ]
    for key, label in (
        ("backend", "Backend"),
        ("goal", "Goal"),
        ("started", "Started"),
        ("limits", "Limits"),
    ):
        if meta.get(key):
            lines.append(f"- {label}: {meta[key]}")
    lines += [
        f"- Ended: **{result.final_phase}** after {_clock(wall)} ({result.samples} samples)",
        "",
        "## Score",
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| Useful work | {_clock(result.useful_s)} ({_share(result.useful_s, wall)} of the run) |",
        f"| Stalled (running, no tokens moving) | {_clock(result.stalled_s)} |",
        f"| Blocked on a permission | {_clock(result.blocked_s)} |",
        f"| Stuck | {_clock(result.stuck_s)} |",
        f"| Paused | {_clock(result.paused_s)} |",
        f"| Planning and waiting for approval | "
        f"{_clock(result.planning_s + result.awaiting_approval_s)} |",
        f"| Minutes a person was needed or nothing moved | {round(result.stuck_total_s / 60)} |",
        f"| Human interventions | {result.human} (plus {result.expected} expected) |",
        f"| Steps ticked | {result.steps_done} of {result.steps_total} |",
        f"| Nudges sent | {result.nudges} |",
        f"| Commits | {result.commits} |",
        f"| Handoffs (context refreshes) | {result.handoffs} |",
        f"| Tokens used | {_count(result.tokens)} |",
        f"| Tokens per day | {_count(result.tokens_per_day)} |",
        f"| Cost per day | {_usd(result.cost_per_day_usd)} |",
        "",
        "Tokens are Autopilot's own figure (input, output and cache writes; cache reads left out). "
        "It is a guard, not a bill.",
        "",
        "## Interventions",
        "",
    ]
    if result.interventions:
        lines += [
            f"- {_clock(i.t)} in: {i.kind}" + (f" ({i.note})" if i.note else "")
            for i in result.interventions
        ]
    else:
        lines.append("None.")
    lines.append("")
    return "\n".join(lines)


def _count(value: int | None) -> str:
    return f"{value:,}" if value is not None else "not known"


def _usd(value: float | None) -> str:
    return f"${value:,.2f}" if value is not None else "not known (no price given)"


# -- a saved run ---------------------------------------------------------------------------------


def dump_run(samples: Iterable[Sample], interventions: Iterable[Intervention]) -> str:
    """One JSON line per record, so a run is appended to as it goes and survives a crash."""
    rows = [{"record": "sample", **s.to_json()} for s in samples]
    rows += [{"record": "intervention", **i.to_json()} for i in interventions]
    return "\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n"


def load_run(text: str) -> tuple[list[Sample], list[Intervention]]:
    """The inverse of :func:`dump_run`; a line that is not a record is skipped."""
    samples: list[Sample] = []
    notes: list[Intervention] = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("record") == "sample":
            samples.append(Sample.from_json(row))
        elif row.get("record") == "intervention":
            notes.append(Intervention.from_json(row))
    return samples, notes
