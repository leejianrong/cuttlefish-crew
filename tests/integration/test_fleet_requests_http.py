"""Integration: the Needs-you HTTP surface (ADR-0028) over a real `FleetDaemon`, broker and
episodic store. Async, because a held request's future lives on the daemon's own loop."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import satay.control

from cuttlefish.episodic.events import RequestRaised, RequestResolved
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.projects.store import ProjectStore
from cuttlefish.requests import PendingRequest

LINE = "docker compose up -d postgres"


class Harness:
    def __init__(self, daemon: FleetDaemon, http: httpx.AsyncClient, project_id: str, root: Path):
        self.daemon, self.http, self.project_id, self.root = daemon, http, project_id, root
        self.team_id = "t-1"

    def raise_request(self, line: str = LINE, *, window_s: float = 60) -> PendingRequest:
        return self.daemon.requests.raise_permission(
            project_id=self.project_id,
            team_id=self.team_id,
            role="builder",
            backend="kopicode",
            line=line,
            why="not on the list",
            window_s=window_s,
        )

    async def answer(self, request_id: str, **body: Any) -> httpx.Response:
        return await self.http.post(
            f"/api/projects/{self.project_id}/requests/{request_id}/answer", json=body
        )

    def journal(self) -> list[Any]:
        store = EpisodicStore.open(self.root / ".cuttlefish" / "episodic.db")
        try:
            return [e.payload for e in store.read(self.team_id)]
        finally:
            store.close()


@pytest.fixture
async def h(tmp_path: Path) -> AsyncIterator[Harness]:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    root = tmp_path / "alpha"
    root.mkdir()
    project = daemon.projects.register(name="alpha", root=str(root))
    daemon.projects.record_team_started(project.id, "t-1", ())
    store = EpisodicStore.open(root / ".cuttlefish" / "episodic.db")
    daemon._team_stores["t-1"] = store
    app = create_app(daemon, security=satay.control.SecurityPolicy(token="tok"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1",
        headers={TOKEN_HEADER: "tok"},
    ) as http:
        yield Harness(daemon, http, project.id, root)
    store.close()


async def test_every_route_needs_the_token(h: Harness) -> None:
    request = h.raise_request()
    anonymous = {TOKEN_HEADER: ""}
    for method, path in (
        ("GET", "/api/requests"),
        ("GET", f"/api/projects/{h.project_id}/requests"),
        ("POST", f"/api/projects/{h.project_id}/requests/{request.id}/answer"),
    ):
        response = await h.http.request(method, path, headers=anonymous, json={"answer": "deny"})
        assert response.status_code in (401, 403), path
    assert h.daemon.requests.pending() == [request]


async def test_the_fleet_list_shows_pending_requests_with_project_and_time_left(h: Harness) -> None:
    request = h.raise_request()
    body = (await h.http.get("/api/requests")).json()["requests"]
    assert len(body) == 1
    row = body[0]
    assert row["id"] == request.id and row["project_name"] == "alpha"
    assert row["role"] == "builder" and row["detail"] == LINE and row["state"] == "pending"
    assert row["answers"] == ["allow_once", "allow_always", "deny"]
    assert row["suggested_rule"] == ["docker", "compose", "up"]
    assert 0 < row["expires_in_s"] <= 60
    assert "_line" not in row


async def test_the_project_list_has_pending_first_then_what_ended(h: Harness) -> None:
    ended = h.raise_request("make test")
    assert (await h.answer(ended.id, answer="deny")).status_code == 200
    waiting = h.raise_request()
    body = (await h.http.get(f"/api/projects/{h.project_id}/requests")).json()
    assert [r["id"] for r in body["pending"]] == [waiting.id]
    assert [(r["id"], r["state"], r["by"]) for r in body["resolved"]] == [
        (ended.id, "denied", "person")
    ]


async def test_an_unknown_project_is_404(h: Harness) -> None:
    assert (await h.http.get("/api/projects/nope/requests")).status_code == 404
    response = await h.http.post("/api/projects/nope/requests/x/answer", json={"answer": "deny"})
    assert response.status_code == 404


async def test_allow_once_releases_the_held_agent(h: Harness) -> None:
    request = h.raise_request()
    held = asyncio.create_task(h.daemon.requests.hold(request))
    response = await h.answer(request.id, answer="allow_once")
    assert response.status_code == 200
    assert response.json() == {
        "request_id": request.id,
        "resolution": "allowed_once",
        "by": "person",
        "rule": None,
        "already": False,
    }
    assert (await held).allows
    assert h.daemon.projects.get(h.project_id).allow == ()  # nothing saved


async def test_always_allow_saves_the_rule_to_the_project_and_grants_the_team(h: Harness) -> None:
    request = h.raise_request()
    response = await h.answer(request.id, answer="allow_always")
    assert response.status_code == 200 and response.json()["rule"] == ["docker", "compose", "up"]
    assert h.daemon.projects.get(h.project_id).allow == (("docker", "compose", "up"),)
    assert h.daemon.requests.grants("t-1") == [("docker", "compose", "up")]
    project = (await h.http.get(f"/api/projects/{h.project_id}")).json()
    assert project["allow"] == [["docker", "compose", "up"]]


async def test_an_always_rule_is_saved_once_however_often_it_is_granted(h: Harness) -> None:
    for _ in range(2):
        request = h.raise_request()
        await h.answer(request.id, answer="allow_always", rule=["docker", "compose"])
    assert h.daemon.projects.get(h.project_id).allow == (("docker", "compose"),)


async def test_an_edited_rule_may_be_narrower_but_not_something_else(h: Harness) -> None:
    request = h.raise_request()
    bad = await h.answer(request.id, answer="allow_always", rule=["docker", "run"])
    assert bad.status_code == 422 and "start of the command" in bad.json()["detail"]
    assert h.daemon.projects.get(h.project_id).allow == ()
    assert h.daemon.requests.pending() == [request]  # still pending: the person can retry
    ok = await h.answer(request.id, answer="allow_always", rule=["docker", "compose"])
    assert ok.status_code == 200


@pytest.mark.parametrize("rule", [["sudo"], ["docker", "compose", "up", "-d", "postgres", "x"], []])
async def test_a_refused_always_saves_nothing(h: Harness, rule: list[str]) -> None:
    request = h.raise_request()
    response = await h.answer(request.id, answer="allow_always", rule=rule)
    assert response.status_code == 422
    assert h.daemon.projects.get(h.project_id).allow == ()
    assert h.daemon.requests.grants("t-1") == []


async def test_a_command_with_shell_syntax_cannot_be_always_allowed(h: Harness) -> None:
    request = h.raise_request("make test && make lint")
    assert request.record.answers == ["allow_once", "deny"]
    response = await h.answer(request.id, answer="allow_always", rule=["make"])
    assert response.status_code == 422


async def test_bad_bodies_are_400_or_422(h: Harness) -> None:
    request = h.raise_request()
    assert (await h.answer(request.id)).status_code == 400
    assert (await h.answer(request.id, answer="deny", rule="make")).status_code == 400
    assert (await h.answer(request.id, answer="maybe")).status_code == 422


async def test_the_same_answer_twice_is_done_and_a_different_one_conflicts(h: Harness) -> None:
    request = h.raise_request()
    assert (await h.answer(request.id, answer="deny")).json()["already"] is False
    again = await h.answer(request.id, answer="deny")
    assert again.status_code == 200 and again.json()["already"] is True
    other = await h.answer(request.id, answer="allow_once")
    assert other.status_code == 409 and other.json()["resolution"] == "denied"


async def test_an_answer_after_the_window_closed_conflicts_as_expired(h: Harness) -> None:
    request = h.raise_request(window_s=0.05)
    await h.daemon.requests.hold(request)
    response = await h.answer(request.id, answer="allow_once")
    assert response.status_code == 409
    assert response.json()["resolution"] == "expired" and response.json()["by"] == "timeout"


async def test_an_answer_to_another_projects_request_is_404(h: Harness, tmp_path: Path) -> None:
    request = h.raise_request()
    other_root = tmp_path / "beta"
    other_root.mkdir()
    other = h.daemon.projects.register(name="beta", root=str(other_root))
    response = await h.http.post(
        f"/api/projects/{other.id}/requests/{request.id}/answer", json={"answer": "deny"}
    )
    assert response.status_code == 404
    assert h.daemon.requests.pending() == [request]


async def test_after_a_restart_the_journal_says_how_a_request_ended(h: Harness) -> None:
    """A fresh daemon has an empty broker; the project's last team's journal answers."""
    store = h.daemon._team_stores["t-1"]
    for rid in ("gone", "dead"):
        store.append(
            "t-1",
            RequestRaised(
                request_id=rid,
                kind="permission",
                title="t",
                detail=LINE,
                why="w",
                answers=["allow_once", "deny"],
                expires_at="2026-10-06T10:00:00+00:00",
            ),
        )
    store.append("t-1", RequestResolved(request_id="gone", resolution="expired", by="timeout"))
    h.daemon.requests.__init__(h.daemon._append_for_team)  # type: ignore[misc]  # a restart

    expired = await h.answer("gone", answer="allow_once")
    assert expired.status_code == 409 and expired.json()["resolution"] == "expired"
    dead = await h.answer("dead", answer="allow_once")
    assert dead.status_code == 409 and dead.json()["resolution"] == "abandoned"
    assert (await h.answer("never-existed", answer="deny")).status_code == 404


async def test_a_question_is_answered_with_text_and_the_journal_keeps_it(h: Harness) -> None:
    request = h.daemon.requests.raise_question(
        project_id=h.project_id,
        team_id=h.team_id,
        role="builder",
        backend="kopicode",
        question="tabs or spaces?",
        context="",
        window_s=60,
    )
    row = (await h.http.get("/api/requests")).json()["requests"][0]
    assert row["kind"] == "question" and row["answers"] == ["answer", "decline"]
    assert (await h.answer(request.id, answer="answer")).status_code == 422  # no text
    assert (await h.answer(request.id, answer="answer", text=5)).status_code == 400
    ok = await h.answer(request.id, answer="answer", text="tabs")
    assert ok.status_code == 200 and ok.json()["resolution"] == "answered"
    again = await h.answer(request.id, answer="answer", text="tabs")
    assert again.status_code == 200 and again.json()["already"] is True
    other = await h.answer(request.id, answer="answer", text="spaces")
    assert other.status_code == 409
    resolved = h.journal()[-1]
    assert isinstance(resolved, RequestResolved) and resolved.text == "tabs"
    listed = (await h.http.get(f"/api/projects/{h.project_id}/requests")).json()["resolved"][0]
    assert listed["state"] == "answered" and listed["text"] == "tabs"
