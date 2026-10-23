"""The driver of the soak harness: runs one scored Autopilot run against a daemon (CUT-136).

It is a client of an already-running ``cuttlefish serve``, over the HTTP API the dashboard uses,
and never the daemon itself. It gives the project's builder a goal with Autopilot on, approves the
plan once (the one thing a person is expected to do), then watches: it samples Autopilot's state,
what is waiting in Needs you, the thread and the timeline, and notes anything a person did that it
did not. It answers no permission and sends no message of its own, so what it scores is what
Autopilot managed alone. At its deadline it stops Autopilot, so a run never goes on spending past
what was agreed. :mod:`cuttlefish.soak` scores what it collected.

Everything that touches the outside (HTTP, the clock, sleeping) is passed in, so a test drives it
against a fake daemon in no real time.
"""

from __future__ import annotations

import dataclasses
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from cuttlefish.autopilot import APPROVAL_TEXT, ENDED, NUDGE_TEXT
from cuttlefish.soak import Intervention, Sample, Score, dump_run, render_report, score

TOKEN_HEADER = "x-cuttlefish-token"
#: The port a person's own daemon uses by default; a soak run is never aimed at it by accident.
DEFAULT_DAEMON_PORT = 8420

#: Rough tokens a long Autopilot run uses per hour, from the two short real runs and the limits;
#: only for the size estimate shown before a run, never a promise.
ESTIMATE_TOKENS_PER_HOUR = 400_000


class SoakError(Exception):
    """The run cannot go on; the message says why."""


class Api(Protocol):
    def get(self, path: str) -> dict[str, Any]: ...

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]: ...


class HttpApi:
    """The daemon's HTTP API with the bearer token, one ``urllib`` request per call."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 60.0) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def get(self, path: str) -> dict[str, Any]:
        return self._call("GET", path, None)

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._call("POST", path, body or {})

    def _call(self, method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self._base + path,
            data=data,
            method=method,
            headers={TOKEN_HEADER: self._token, "content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise SoakError(f"{method} {path} answered {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SoakError(f"{method} {path} could not reach the daemon: {exc}") from exc
        if not isinstance(payload, dict):
            raise SoakError(f"{method} {path} did not answer with an object")
        return payload


@dataclasses.dataclass(frozen=True)
class RunPlan:
    """What a run is going to do and spend: shown, and agreed to, before it starts."""

    project_id: str
    goal: str
    backend: str
    hours: float
    tokens: int
    nudges: int
    arm: str = "baseline"
    #: Seconds between samples, and between the slower reads of the thread and timeline.
    interval_s: float = 30.0
    slow_every: int = 4
    usd_per_mtok: float | None = None

    def estimate(self) -> str:
        """The size of the run in words, for the question before it is started."""
        likely = int(self.hours * ESTIMATE_TOKENS_PER_HOUR)
        ceiling = min(self.tokens, likely * 3)
        return (
            f"A real {self.backend} run for up to {self.hours:g} hours on project "
            f"{self.project_id}. It uses the {self.backend} login or key that daemon has. "
            f"Expect very roughly {likely:,} tokens, and at most about {ceiling:,}; Autopilot "
            f"itself stops at {self.tokens:,} tokens or {self.nudges} nudges. Nothing is answered "
            "for the builder: a permission it asks for waits, and counts against the score."
        )


def check_target(base_url: str, *, allow_default_port: bool) -> None:
    """Refuse the default daemon port unless it was asked for, so a person's own daemon and
    their real projects are not made the subject of a soak run by a copied command."""
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise SoakError(f"{base_url!r} is not an http(s) URL")
    if parsed.port == DEFAULT_DAEMON_PORT and not allow_default_port:
        raise SoakError(
            f"port {DEFAULT_DAEMON_PORT} is the default daemon's. Run a scratch daemon on "
            "another port (see agent_docs/exploratory-testing.md), or pass --allow-default-port "
            "if you do mean this one."
        )


def _num(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def read_sample(
    api: Api,
    plan: RunPlan,
    t: float,
    *,
    slow: bool,
    carried: Sample | None,
    started_iso: str,
) -> tuple[Sample, list[dict[str, Any]]]:
    """One sample. The slow reads (timeline, thread) are repeated from ``carried`` between turns."""
    pid = plan.project_id
    status = api.get(f"/api/projects/{pid}/autopilot")
    pending = api.get(f"/api/projects/{pid}/requests").get("pending", [])
    steps = status.get("plan") or {}
    commits = carried.commits if carried else 0
    refreshes = carried.refreshes if carried else 0
    if slow:
        timeline = api.get(f"/api/projects/{pid}/overview?since={started_iso}").get("timeline", [])
        events = [
            e for e in timeline if isinstance(e, dict) and str(e.get("at", "")) >= started_iso
        ]
        commits = sum(1 for e in events if e.get("kind") == "commit")
        refreshes = sum(1 for e in events if e.get("kind") == "refresh")
    tokens = status.get("tokens_used")
    sample = Sample(
        t=t,
        phase=str(status.get("phase", "off")),
        steps_done=_num(steps.get("done")),
        steps_total=_num(steps.get("total")),
        nudges=_num(status.get("nudges")),
        tokens=tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None,
        waiting=len(pending) if isinstance(pending, list) else 0,
        commits=commits,
        refreshes=refreshes,
    )
    return sample, pending if isinstance(pending, list) else []


def person_messages(items: list[dict[str, Any]], goal: str) -> list[tuple[str, str]]:
    """The thread's user messages, as (id, text), that neither Autopilot nor the goal sent."""
    ours = (APPROVAL_TEXT[:40], NUDGE_TEXT[:40], goal[:40])
    out = []
    for item in items:
        text = item.get("text")
        if (
            item.get("kind") == "user"
            and isinstance(text, str)
            and text.strip()
            and not any(text.lstrip().startswith(prefix) for prefix in ours if prefix)
        ):
            out.append((str(item.get("id") or text), text))
    return out


@dataclasses.dataclass
class Outcome:
    result: Score
    samples: list[Sample]
    interventions: list[Intervention]
    report: str


def run_soak(
    api: Api,
    plan: RunPlan,
    *,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    now_iso: Callable[[], str],
    save: Callable[[str], None] | None = None,
    log: Callable[[str], None] = lambda _: None,
) -> Outcome:
    """Start the goal, watch it to the end or the deadline, and score it.

    ``save`` is given the whole run so far (see :func:`cuttlefish.soak.dump_run`) after each sample,
    so a crash of the driver loses nothing. The daemon's builder keeps going if the driver dies.
    """
    pid = plan.project_id
    before = api.get(f"/api/projects/{pid}/autopilot")
    if str(before.get("phase")) in ("planning", "awaiting_approval", "running", "paused"):
        raise SoakError(f"project {pid} already has an Autopilot run ({before.get('phase')}).")

    started_iso = now_iso()
    start = clock()
    api.post(
        f"/api/projects/{pid}/goal",
        {
            "text": plan.goal,
            "backend": plan.backend,
            "autopilot": {"hours": plan.hours, "tokens": plan.tokens, "nudges": plan.nudges},
        },
    )
    log(f"goal given at {started_iso}")

    samples: list[Sample] = []
    notes: list[Intervention] = []
    deadline = start + plan.hours * 3600
    last_phase = ""
    last_waiting = 0
    seen_messages: set[str] = set()
    approved = False
    tick = 0
    while True:
        t = clock() - start
        slow = tick % plan.slow_every == 0
        sample, _ = read_sample(
            api,
            plan,
            t,
            slow=slow,
            carried=samples[-1] if samples else None,
            started_iso=started_iso,
        )
        # A request that was waiting and no longer is was answered by a person (never the driver).
        if last_waiting > sample.waiting:
            notes.append(Intervention(t, "permission", f"{last_waiting - sample.waiting} answered"))
        last_waiting = sample.waiting
        # A run that moved back to running from a stop was resumed by a person.
        if last_phase in ("paused", "stuck", "limit") and sample.phase == "running":
            notes.append(Intervention(t, "resume", f"from {last_phase}"))
        if slow and sample.phase != "off":
            thread = api.get(f"/api/projects/{pid}/chat")
            said = person_messages(thread.get("items", []), plan.goal)
            for key, text in said:
                if key not in seen_messages:
                    seen_messages.add(key)
                    notes.append(Intervention(t, "message", text[:80]))
        last_phase = sample.phase
        samples.append(sample)

        if sample.phase == "awaiting_approval" and not approved:
            api.post(f"/api/projects/{pid}/autopilot/approve")
            approved = True
            notes.append(Intervention(t, "approve_plan", "by the harness"))
            log(f"plan approved at {t / 60:.0f} min")
        if save is not None:
            save(dump_run(samples, notes))

        if sample.phase in ENDED:
            log(f"run ended: {sample.phase}")
            break
        if clock() >= deadline:
            api.post(f"/api/projects/{pid}/autopilot/stop")
            notes.append(Intervention(clock() - start, "harness_stop", "deadline reached"))
            samples.append(
                dataclasses.replace(sample, t=clock() - start, phase="stopped", waiting=0)
            )
            log("deadline reached; Autopilot stopped")
            break
        tick += 1
        sleep(plan.interval_s)

    if save is not None:
        save(dump_run(samples, notes))
    result = score(samples, notes, arm=plan.arm, usd_per_mtok=plan.usd_per_mtok)
    meta = {
        "title": f"{plan.arm} on {pid}",
        "backend": plan.backend,
        "goal": plan.goal if len(plan.goal) <= 200 else plan.goal[:199] + "…",
        "started": started_iso,
        "limits": f"{plan.hours:g} h, {plan.tokens:,} tokens, {plan.nudges} nudges",
    }
    return Outcome(result, samples, notes, render_report(result, meta))


def write_outputs(out_dir: Path, outcome: Outcome) -> list[Path]:
    """The report and the raw run, side by side, so the run can be scored again."""
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "report.md"
    report.write_text(outcome.report)
    summary = out_dir / "score.json"
    summary.write_text(json.dumps(outcome.result.to_json(), indent=2, sort_keys=True) + "\n")
    run = out_dir / "run.jsonl"
    run.write_text(dump_run(outcome.samples, outcome.interventions))
    return [report, summary, run]
