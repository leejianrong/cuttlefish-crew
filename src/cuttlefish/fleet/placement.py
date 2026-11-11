"""Teams that run on a worker machine, as the controller tracks them (ADR-0057, CUT-206).

The controller still decides *what* runs and records everything it reads back; the worker only does
the running. A team placed on a worker is an order waiting for that worker's next poll, then a team
the worker reports on: its journal events stream in and are appended to the one journal here (never
a second copy), and it says when it is done.

This table is in memory, and is rebuilt from the project store after a controller restart
(`FleetDaemon.adopt_remote_teams`). Each poll says which teams the worker is running; one the
controller placed there that it is not running (the worker restarted, or an order was lost with a
dropped link) is handed over again, marked `resume`, so the worker picks its own satay run up where
it stopped (CUT-207).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RemoteTeam:
    team_id: str
    project_id: str
    worker_id: str
    #: What the worker is to run, until it has taken it.
    order: dict[str, Any] | None
    #: The order as first made, kept so it can be handed over again to resume.
    base_order: dict[str, Any] | None = None
    #: The token of the hand-over the worker now has; its reports must carry it (CUT-300). `None`
    #: for a team taken back after a restart whose worker has not reported yet.
    dispatch: str | None = None
    stop_requested: bool = False
    stop_sent: bool = False
    #: The last journal position from the worker already appended here, so a repeated post of the
    #: same events changes nothing.
    last_seq: int = 0


class Placement:
    def __init__(self) -> None:
        self._teams: dict[str, RemoteTeam] = {}  # by project id: one team per project
        #: `worker id -> (name, online)`, or `None` for a worker nobody knows. Set by the server
        #: once there is a worker registry; with none, nothing can be placed.
        self.lookup: Callable[[str], tuple[str, bool] | None] = lambda _id: None

    def assign(self, project_id: str, worker_id: str, order: dict[str, Any]) -> RemoteTeam:
        order = {**order, "dispatch": uuid.uuid4().hex}
        team = RemoteTeam(order["team_id"], project_id, worker_id, order, order, order["dispatch"])
        self._teams[project_id] = team
        return team

    def adopt(
        self, project_id: str, worker_id: str, order: dict[str, Any], last_seq: int
    ) -> RemoteTeam:
        """A team that was out on a worker before a controller restart. Whether the worker still
        runs it is learned from its next poll, so it starts as an order to resume."""
        resume = {**order, "resume": True, "dispatch": uuid.uuid4().hex}
        team = RemoteTeam(order["team_id"], project_id, worker_id, resume, order)
        team.last_seq = last_seq
        self._teams[project_id] = team
        return team

    def team_of_project(self, project_id: str) -> RemoteTeam | None:
        return self._teams.get(project_id)

    def team(self, team_id: str) -> RemoteTeam | None:
        return next((t for t in self._teams.values() if t.team_id == team_id), None)

    def teams_on(self, worker_id: str) -> list[RemoteTeam]:
        return [t for t in self._teams.values() if t.worker_id == worker_id]

    def end(self, team_id: str) -> RemoteTeam | None:
        team = self.team(team_id)
        if team is not None:
            del self._teams[team.project_id]
        return team

    def reconcile(self, worker_id: str, running: set[str]) -> None:
        """Compare what the worker says it runs with what was placed on it."""
        for team in self.teams_on(worker_id):
            if team.team_id in running:
                team.order = None
            elif team.order is None and team.base_order is not None:
                team.order = {**team.base_order, "resume": True, "dispatch": uuid.uuid4().hex}
                team.dispatch = team.order["dispatch"]
                team.stop_sent = False  # a stop the dead process never acted on goes out again

    async def claim(
        self, worker_id: str, wait: float, running: set[str] | None = None
    ) -> dict[str, Any]:
        """What this worker should do now: orders to start and teams to stop, each handed over once.
        Waits up to `wait` seconds for something, so a start reaches an idle worker quickly without
        it polling hard. `running` is the teams the worker says it has going; without it nothing
        is handed over again."""
        if running is not None:
            self.reconcile(worker_id, running)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while True:
            orders: list[dict[str, Any]] = []
            stops: list[str] = []
            for team in self.teams_on(worker_id):
                if team.order is not None:
                    orders.append(team.order)
                    if team.order.get("dispatch"):
                        team.dispatch = team.order["dispatch"]
                    team.order = None
                if team.stop_requested and not team.stop_sent:
                    team.stop_sent = True
                    stops.append(team.team_id)
            if orders or stops or loop.time() >= deadline:
                return {"orders": orders, "stop": stops}
            await asyncio.sleep(0.2)
