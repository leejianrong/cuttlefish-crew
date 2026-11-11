"""What a worker does with a team the controller placed on it (ADR-0057, CUT-206).

The team is the same `run_team` workflow `cuttlefish run-team` and the daemon run, started here,
in a checkout on this machine, against a local journal and a local satay store. The controller keeps
the real journal: this module streams every new event to it (the controller appends them, redacting
at write time) and tells it when the team is over. A stop arrives as an order and ends the satay run
the same way the daemon's own Stop does.

Nothing the controller sends is a secret or a model key. The agent here uses the credentials this
machine already has; the leased model key is not built yet, and so a project's own secrets are not
sent either.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import satay
import satay.control

from cuttlefish import runtime
from cuttlefish.config import ConfigError, prepare_run
from cuttlefish.episodic.events import StopRequested, encode_payload
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.steering import cancel_run
from cuttlefish.team import TeamInput, run_team
from cuttlefish.worker.link import LinkError, WorkerLink

_LOG = logging.getLogger(__name__)

#: How often a running team's new events are sent, and how many go in one request.
STREAM_EVERY_SECONDS = 0.5
_BATCH = 200
_GIT_TIMEOUT = 600


class RefusedError(Exception):
    """The controller turned a report down (it no longer knows this team, as after a restart, or
    did not like what was sent): the worker drops the team."""


def checkout(workdir: Path, project_id: str, repo: str | None) -> Path:
    """This machine's working copy of a project: cloned from its `repo` the first time, or an
    empty repository when the project has no remote. The checkout is the work, and stays."""
    path = workdir / re.sub(r"[^A-Za-z0-9_-]", "_", project_id)
    if (path / ".git").exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    if repo:
        cmd = ["git", "clone", "--quiet", "--", repo, str(path)]
    else:
        path.mkdir(exist_ok=True)
        cmd = ["git", "-C", str(path), "init", "--quiet"]
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=_GIT_TIMEOUT)
    return path


class Executor:
    """Runs the teams placed on this worker, one task each, over whatever link is current."""

    def __init__(self, workdir: Path, say: Callable[[str], None]) -> None:
        self._workdir = workdir
        self._say = say
        #: The current connection to the controller; `None` while it is down (a team keeps going
        #: and its events wait their turn).
        self.link: WorkerLink | None = None
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._stops: dict[str, Callable[[], None]] = {}
        self._stopping: set[str] = set()
        self._background: set[asyncio.Task[None]] = set()

    def start(self, order: dict[str, Any]) -> None:
        team_id = str(order["team_id"])
        if team_id not in self._jobs:
            self._say(f"Starting a team for {order.get('name', 'a project')}.")
            self._jobs[team_id] = asyncio.create_task(self._job(order))

    def stop(self, team_id: str) -> None:
        self._stopping.add(team_id)
        request = self._stops.get(team_id)
        if request is not None:
            request()

    async def close(self) -> None:
        for task in self._jobs.values():
            task.cancel()
        await asyncio.gather(*self._jobs.values(), return_exceptions=True)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """One request to the controller, waiting for a connection when there is none."""
        while True:
            link = self.link
            if link is not None and not link.closed.is_set():
                try:
                    status, data = await link.request("POST", path, body)
                except LinkError:
                    status, data = 0, None
                if 400 <= status < 500:
                    raise RefusedError
                if status == 200:
                    return dict(data or {})
            await asyncio.sleep(0.5)

    async def _job(self, order: dict[str, Any]) -> None:
        team_id = str(order["team_id"])
        status, error = "completed", ""
        try:
            status, error = await self._drive(order)
        except RefusedError:
            self._say("The controller turned this team's report down; dropping the team.")
            return
        except asyncio.CancelledError:
            raise
        except (ConfigError, OSError, subprocess.SubprocessError) as exc:
            status, error = "failed", str(exc)[:300]
        except Exception as exc:
            _LOG.exception("team %s failed on this worker", team_id)
            status, error = "failed", f"{type(exc).__name__}: {exc}"[:300]
        finally:
            self._jobs.pop(team_id, None)
            self._stops.pop(team_id, None)
        with contextlib.suppress(RefusedError):
            await self._post(
                f"/api/worker/teams/{team_id}/finish", {"status": status, "error": error}
            )
        self._say(f"Team for {order.get('name', 'a project')} ended ({status}).")

    async def _drive(self, order: dict[str, Any]) -> tuple[str, str]:
        team_id = str(order["team_id"])
        roles = order["roles"]
        root = await asyncio.to_thread(
            checkout, self._workdir, str(order["project_id"]), order.get("repo")
        )
        prepared = prepare_run(
            project=str(order.get("secrets_scope") or order["project_id"]),
            secret_names=[],
            base_dir=root,
            agent_backend=order.get("backend"),
            extra_backends=[r["backend"] for r in roles if r.get("backend")],
        )
        try:
            runtime.configure(prepared.as_runtime())
            workflow_input: TeamInput = {
                "team_id": team_id,
                "root": str(root),
                "project": str(order.get("secrets_scope") or order["project_id"]),
                "roles": roles,
                "steerable": True,
                "require_approval": bool(order.get("require_approval")),
            }
            if order.get("max_tokens") is not None:
                workflow_input["max_tokens"] = order["max_tokens"]
            if order.get("max_cost_usd") is not None:
                workflow_input["max_cost_usd"] = order["max_cost_usd"]
            streamer: asyncio.Task[None] | None = None
            failed = False
            async with satay.control.run_app(data_dir=root / ".satay") as app:

                def request_stop() -> None:
                    task = asyncio.create_task(self._cancel(prepared.episodic_store, app, team_id))
                    self._background.add(task)
                    task.add_done_callback(self._background.discard)

                self._stops[team_id] = request_stop
                if team_id in self._stopping:
                    request_stop()
                streamer = asyncio.create_task(self._stream(prepared.episodic_store, team_id))
                handle = satay.start(run_team, workflow_input, run_id=team_id, store=app.store)
                try:
                    await handle.result()
                except satay.WorkflowFailedError:
                    failed = True
                except RuntimeError:
                    # A stop cancels the run between rounds, so satay finds no terminal event.
                    if team_id not in self._stopping:
                        raise
            streamer.cancel()
            await asyncio.gather(streamer, return_exceptions=True)
            await self._flush(prepared.episodic_store, team_id, 0)
            if team_id in self._stopping:
                return "stopped", ""
            return (
                ("failed", "the team's workflow failed; see its journal")
                if failed
                else ("completed", "")
            )
        finally:
            prepared.close()

    async def _cancel(self, store: EpisodicStore, app: Any, team_id: str) -> None:
        # In-process, so off the loop: the control API this calls is served by this same loop.
        store.append(team_id, StopRequested())
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                cancel_run, base_url=app.base_url, token=app.token, run_id=team_id
            )

    async def _stream(self, store: EpisodicStore, team_id: str) -> None:
        sent = 0
        while True:
            sent = await self._flush(store, team_id, sent)
            await asyncio.sleep(STREAM_EVERY_SECONDS)

    async def _flush(self, store: EpisodicStore, team_id: str, sent: int) -> int:
        """Send every event past `sent`; returns the position reached. A request that fails is
        tried again, so nothing is dropped and the controller ignores a repeat."""
        while True:
            # On the loop: this store's connection belongs to it, and a read is small.
            events = list(store.read(team_id, after=sent))[:_BATCH]
            if not events:
                return sent
            body = {
                "events": [
                    {
                        "seq": e.seq,
                        "ts": e.ts.isoformat(),
                        **dict(zip(("type", "data"), encode_payload(e.payload), strict=True)),
                    }
                    for e in events
                ]
            }
            await self._post(f"/api/worker/teams/{team_id}/events", body)
            sent = events[-1].seq
