"""Teams that run on a worker machine, as the controller tracks them (ADR-0057, CUT-206).

The controller still decides *what* runs and records everything it reads back; the worker only does
the running. A team placed on a worker is an order waiting for that worker's next poll, then a team
the worker reports on: its journal events stream in and are appended to the one journal here (never
a second copy), and it says when it is done. All of this is in memory: a controller that restarts
forgets what it handed out, the worker's next report for such a team is refused, and the worker
stops it. (Picking a team back up after a restart is CUT-207.)
"""

from __future__ import annotations

import asyncio
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
        team = RemoteTeam(order["team_id"], project_id, worker_id, order)
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

    async def claim(self, worker_id: str, wait: float) -> dict[str, Any]:
        """What this worker should do now: orders to start and teams to stop, each handed over once.
        Waits up to `wait` seconds for something, so a start reaches an idle worker quickly without
        it polling hard."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while True:
            orders: list[dict[str, Any]] = []
            stops: list[str] = []
            for team in self.teams_on(worker_id):
                if team.order is not None:
                    orders.append(team.order)
                    team.order = None
                if team.stop_requested and not team.stop_sent:
                    team.stop_sent = True
                    stops.append(team.team_id)
            if orders or stops or loop.time() >= deadline:
                return {"orders": orders, "stop": stops}
            await asyncio.sleep(0.2)
