"""Integration (CUT-127, ADR-0040): a Claude Code session's hooks raise Needs you cards.

A real daemon, broker and journal; the hook route is called the way the hook script calls it, and a
person answers through the same routes the dashboard uses. Held hooks and answers share one loop."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import satay.control

from cuttlefish.fleet import claude_hooks
from cuttlefish.fleet.claude_hooks import MAX_PENDING, session_scope
from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.server import HOOK_TOKEN_HEADER, TOKEN_HEADER, create_app
from cuttlefish.projects.store import ProjectStore

OWNER = {TOKEN_HEADER: "loopback"}
HOOK = {HOOK_TOKEN_HEADER: "hook-token-for-tests"}


@dataclass
class Rig:
    daemon: FleetDaemon
    http: httpx.AsyncClient
    project_id: str
    root: Path

    async def hook(self, event: str, **fields: Any) -> dict[str, Any]:
        response = await self.http.post(
            f"/api/hooks/claude-code/{self.project_id}",
            headers=HOOK,
            json={"hook_event_name": event, **fields},
        )
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def start(self, event: str, **fields: Any) -> asyncio.Task[dict[str, Any]]:
        return asyncio.create_task(self.hook(event, **fields))

    async def cards(self) -> list[dict[str, Any]]:
        response = await self.http.get(f"/api/projects/{self.project_id}/requests", headers=OWNER)
        pending: list[dict[str, Any]] = response.json()["pending"]
        return pending

    async def history(self) -> list[dict[str, Any]]:
        response = await self.http.get(f"/api/projects/{self.project_id}/requests", headers=OWNER)
        resolved: list[dict[str, Any]] = response.json()["resolved"]
        return resolved

    async def one_card(self, timeout: float = 5) -> dict[str, Any]:
        async with asyncio.timeout(timeout):
            while not (cards := await self.cards()):
                await asyncio.sleep(0.02)
        assert len(cards) == 1, cards
        return cards[0]

    async def answer(self, card: dict[str, Any], answer: str, **extra: Any) -> httpx.Response:
        return await self.http.post(
            f"/api/projects/{self.project_id}/requests/{card['id']}/answer",
            headers=OWNER,
            json={"answer": answer, **extra},
        )

    def set_mode(self, mode: str) -> None:
        self.daemon.projects.update_mode(self.project_id, mode)


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    store = ProjectStore.open(tmp_path / "projects.db")
    root = tmp_path / "alpha"
    root.mkdir()
    project = store.register(name="alpha", root=str(root), backend="claude-code")
    daemon = FleetDaemon(store, request_window_s=30)
    app = create_app(
        daemon,
        security=satay.control.SecurityPolicy(token="loopback"),
        hook_token=HOOK[HOOK_TOKEN_HEADER],
    )
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")
    try:
        yield Rig(daemon, http, project.id, root)
    finally:
        await http.aclose()
        await daemon.close()
        store.close()


# -- what is decided without asking ----------------------------


async def test_a_command_on_the_list_runs_and_a_never_allowed_one_is_refused_with_no_card(
    rig: Rig,
) -> None:
    assert await rig.hook(
        "PermissionRequest", tool_name="Bash", tool_input={"command": "ls -la"}
    ) == {"decision": "allow"}
    refused = await rig.hook(
        "PermissionRequest", tool_name="Bash", tool_input={"command": "sudo rm -rf /"}
    )
    assert refused["decision"] == "deny" and "never_allowed" in refused["message"]
    assert await rig.cards() == []


async def test_edits_inside_the_project_are_allowed_and_outside_it_are_refused(rig: Rig) -> None:
    inside = str(rig.root / "src" / "a.py")
    assert (
        await rig.hook("PermissionRequest", tool_name="Write", tool_input={"file_path": inside})
    )["decision"] == "allow"
    assert (
        await rig.hook("PermissionRequest", tool_name="Edit", tool_input={"file_path": "rel/b.py"})
    )["decision"] == "allow"
    for path in ("/etc/passwd", str(rig.root / ".." / "elsewhere" / "x")):
        out = await rig.hook("PermissionRequest", tool_name="Write", tool_input={"file_path": path})
        assert out["decision"] == "deny" and "outside the project folder" in out["message"]
    assert await rig.cards() == []


async def test_auto_mode_and_a_question_are_left_to_the_terminal(rig: Rig) -> None:
    rig.set_mode("auto")
    assert (
        await rig.hook("PermissionRequest", tool_name="Bash", tool_input={"command": "make x"})
        == {}
    )
    rig.set_mode("standard")
    assert await rig.hook("PermissionRequest", tool_name="AskUserQuestion", tool_input={}) == {}
    assert await rig.cards() == []


# -- a card, held until a person answers ----------------------------


async def test_a_command_off_the_list_is_a_card_and_allow_once_lets_it_run(rig: Rig) -> None:
    held = rig.start(
        "PermissionRequest", tool_name="Bash", tool_input={"command": "docker compose up -d"}
    )
    card = await rig.one_card()
    assert card["kind"] == "permission" and card["detail"] == "docker compose up -d"
    assert card["title"] == "Claude Code wants to run a command that isn't on the list"
    assert card["backend"] == "claude-code" and card["answers"] == [
        "allow_once",
        "allow_always",
        "deny",
    ]
    assert "not on this project's command list" in card["why"].lower()
    assert not held.done()  # the hook is still holding; the terminal dialog is still on screen
    assert (await rig.answer(card, "allow_once")).status_code == 200
    assert await asyncio.wait_for(held, 5) == {"decision": "allow"}
    ended = (await rig.history())[0]
    assert ended["state"] == "allowed_once" and ended["id"] == card["id"]


async def test_deny_tells_the_hook_to_refuse(rig: Rig) -> None:
    held = rig.start("PermissionRequest", tool_name="Bash", tool_input={"command": "make deploy"})
    card = await rig.one_card()
    await rig.answer(card, "deny")
    out = await asyncio.wait_for(held, 5)
    assert out["decision"] == "deny" and "Needs you" in out["message"]


async def test_always_allow_saves_the_rule_and_the_next_command_needs_no_card(rig: Rig) -> None:
    held = rig.start(
        "PermissionRequest", tool_name="Bash", tool_input={"command": "make deploy now"}
    )
    card = await rig.one_card()
    assert card["suggested_rule"] == ["make", "deploy", "now"][: len(card["suggested_rule"])]
    assert (await rig.answer(card, "allow_always", rule=["make", "deploy"])).status_code == 200
    assert (await asyncio.wait_for(held, 5)) == {"decision": "allow"}
    again = await rig.hook(
        "PermissionRequest", tool_name="Bash", tool_input={"command": "make deploy staging"}
    )
    assert again == {"decision": "allow"} and await rig.cards() == []
    assert ["make", "deploy"] in [list(a) for a in rig.daemon.projects.get(rig.project_id).allow]


async def test_ask_first_asks_about_every_command(rig: Rig) -> None:
    rig.set_mode("ask-first")
    held = rig.start("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"})
    card = await rig.one_card()
    assert "waits for you" in card["why"].lower()
    await rig.answer(card, "allow_once")
    assert await asyncio.wait_for(held, 5) == {"decision": "allow"}


async def test_any_other_tool_is_a_card_with_allow_once_and_deny_only(rig: Rig) -> None:
    held = rig.start(
        "PermissionRequest", tool_name="WebFetch", tool_input={"url": "https://example.org/x"}
    )
    card = await rig.one_card()
    assert card["title"] == "Claude Code wants to use WebFetch"
    assert card["answers"] == ["allow_once", "deny"] and card["suggested_rule"] is None
    assert "example.org" in card["detail"]
    await rig.answer(card, "deny")
    assert (await asyncio.wait_for(held, 5))["decision"] == "deny"


async def test_a_secret_in_a_command_is_redacted_on_the_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Built from parts: a fixture shaped like a real provider key trips the secret scan.
    fixture = "redaction-fixture-" + "ab" * 12
    monkeypatch.setenv("OPENROUTER_API_KEY", fixture)
    store = ProjectStore.open(tmp_path / "p.db")
    root = tmp_path / "r"
    root.mkdir()
    project = store.register(name="r", root=str(root), backend="claude-code")
    daemon = FleetDaemon(store, request_window_s=30)
    app = create_app(
        daemon, security=satay.control.SecurityPolicy(token="loopback"), hook_token="t" * 40
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as http:
        held = asyncio.create_task(
            http.post(
                f"/api/hooks/claude-code/{project.id}",
                headers={HOOK_TOKEN_HEADER: "t" * 40},
                json={
                    "hook_event_name": "PermissionRequest",
                    "tool_name": "Bash",
                    "tool_input": {"command": f"deploy --key {fixture}"},
                },
            )
        )
        await asyncio.sleep(0.3)
        cards = (await http.get(f"/api/projects/{project.id}/requests", headers=OWNER)).json()[
            "pending"
        ]
        assert fixture not in json.dumps(cards)
        assert "[redacted:OPENROUTER_API_KEY]" in cards[0]["detail"]
        held.cancel()
        await asyncio.gather(held, return_exceptions=True)
    await daemon.close()
    store.close()


# -- a card the terminal answered, or that nobody can answer ----------------------------


async def test_a_command_allowed_in_the_terminal_clears_its_card(rig: Rig) -> None:
    call = {"command": "docker compose build"}
    await rig.hook("PreToolUse", tool_name="Bash", tool_input=call, tool_use_id="toolu_1")
    held = rig.start("PermissionRequest", tool_name="Bash", tool_input=call)
    card = await rig.one_card()
    await rig.hook("PostToolUse", tool_name="Bash", tool_input=call, tool_use_id="toolu_1")
    assert await asyncio.wait_for(held, 5) == {}  # no decision: the terminal already decided
    ended = (await rig.history())[0]
    assert ended["id"] == card["id"] and ended["state"] == "superseded"
    assert ended["by"] == "person"


async def test_a_command_denied_in_the_terminal_is_found_in_the_transcript(
    rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claude_hooks, "_TAIL_EVERY_S", 0.05)
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"type":"user","message":{"content":"earlier"}}\n')
    call = {"command": "make nuke"}
    await rig.hook("PreToolUse", tool_name="Bash", tool_input=call, tool_use_id="toolu_9")
    held = rig.start(
        "PermissionRequest",
        tool_name="Bash",
        tool_input=call,
        transcript_path=str(transcript),
    )
    await rig.one_card()
    # A refusal of some other call does not end this card.
    other = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_other",
                    "is_error": True,
                    "content": "The user doesn't want to proceed with this tool use.",
                }
            ]
        },
    }
    with transcript.open("a") as handle:
        handle.write(json.dumps(other) + "\n")
    await asyncio.sleep(0.4)
    assert not held.done()
    mine = json.loads(json.dumps(other).replace("toolu_other", "toolu_9"))
    with transcript.open("a") as handle:
        handle.write(json.dumps(mine) + "\n")
    assert await asyncio.wait_for(held, 5) == {}
    assert (await rig.history())[0]["state"] == "superseded"


@pytest.mark.parametrize("event", ["UserPromptSubmit", "Stop"])
async def test_the_next_prompt_or_the_end_of_the_turn_clears_every_card(
    rig: Rig, event: str
) -> None:
    first = rig.start("PermissionRequest", tool_name="Bash", tool_input={"command": "make a"})
    await rig.one_card()
    assert await rig.hook(event) == {}
    assert await asyncio.wait_for(first, 5) == {}
    assert await rig.cards() == []


async def test_a_card_nobody_answers_expires_and_leaves_the_terminal_to_ask(rig: Rig) -> None:
    rig.daemon._request_window_s = 0.3
    out = await asyncio.wait_for(
        rig.hook("PermissionRequest", tool_name="Bash", tool_input={"command": "make slow"}), 5
    )
    assert out == {}
    assert (await rig.history())[0]["state"] == "expired"


async def test_a_hook_that_went_away_abandons_its_card(rig: Rig) -> None:
    held = rig.start("PermissionRequest", tool_name="Bash", tool_input={"command": "make gone"})
    await rig.one_card()
    held.cancel()
    await asyncio.gather(held, return_exceptions=True)
    async with asyncio.timeout(5):
        while await rig.cards():
            await asyncio.sleep(0.02)
    assert (await rig.history())[0]["state"] == "abandoned"


async def test_stopping_the_session_ends_its_cards_and_it_asks_nobody_until_started_again(
    rig: Rig,
) -> None:
    held = rig.start("PermissionRequest", tool_name="Bash", tool_input={"command": "make one"})
    await rig.one_card()
    rig.daemon._claude_hooks.end_session(rig.project_id)
    assert await asyncio.wait_for(held, 5) == {}
    assert (await rig.history())[0]["state"] == "cancelled"
    # Nothing is raised for a stopped session: a late hook gets no decision and no card.
    assert (
        await rig.hook("PermissionRequest", tool_name="Bash", tool_input={"command": "make two"})
        == {}
    )
    assert await rig.cards() == []
    rig.daemon.requests.reopen(session_scope(rig.project_id))
    again = rig.start("PermissionRequest", tool_name="Bash", tool_input={"command": "make three"})
    await rig.one_card()
    again.cancel()
    await asyncio.gather(again, return_exceptions=True)


async def test_no_more_than_twenty_cards_wait_at_once(rig: Rig) -> None:
    held = [
        rig.start("PermissionRequest", tool_name="WebFetch", tool_input={"url": f"https://e/{i}"})
        for i in range(MAX_PENDING)
    ]
    async with asyncio.timeout(10):
        while len(await rig.cards()) < MAX_PENDING:
            await asyncio.sleep(0.05)
    extra = await rig.hook(
        "PermissionRequest", tool_name="WebFetch", tool_input={"url": "https://e/x"}
    )
    assert extra == {} and len(await rig.cards()) == MAX_PENDING
    for task in held:
        task.cancel()
    await asyncio.gather(*held, return_exceptions=True)


async def test_a_restart_abandons_what_the_session_left_pending(rig: Rig) -> None:
    held = rig.start("PermissionRequest", tool_name="Bash", tool_input={"command": "make lost"})
    await rig.one_card()
    # The daemon goes away with the card unresolved; a new one sweeps it (ADR-0028).
    held.cancel()
    await asyncio.gather(held, return_exceptions=True)
    rig.daemon.sweep_abandoned()
    events = rig.daemon._session_events(rig.daemon.projects.get(rig.project_id))
    from cuttlefish.requests import unresolved

    assert unresolved(events) == []


# -- who may call it ----------------------------


async def test_only_the_hook_token_opens_the_hook_route_and_it_opens_nothing_else(rig: Rig) -> None:
    url = f"/api/hooks/claude-code/{rig.project_id}"
    body = {"hook_event_name": "Stop"}
    assert (await rig.http.post(url, json=body)).status_code == 401
    assert (
        await rig.http.post(url, headers={HOOK_TOKEN_HEADER: "wrong"}, json=body)
    ).status_code == 401
    assert (await rig.http.post(url, headers=OWNER, json=body)).status_code == 401
    assert (await rig.http.post(url, headers=HOOK, json=body)).status_code == 200
    # The hook token is not the owner's: it cannot read or answer anything.
    assert (await rig.http.get("/api/projects", headers=HOOK)).status_code == 401
    held = rig.start("PermissionRequest", tool_name="Bash", tool_input={"command": "make z"})
    card = await rig.one_card()
    assert (
        await rig.http.post(
            f"/api/projects/{rig.project_id}/requests/{card['id']}/answer",
            headers=HOOK,
            json={"answer": "allow_once"},
        )
    ).status_code == 401
    held.cancel()
    await asyncio.gather(held, return_exceptions=True)


async def test_an_unknown_project_is_a_404_and_an_oversized_payload_is_refused(rig: Rig) -> None:
    assert (
        await rig.http.post(
            "/api/hooks/claude-code/nope", headers=HOOK, json={"hook_event_name": "Stop"}
        )
    ).status_code == 404
    big = await rig.http.post(
        f"/api/hooks/claude-code/{rig.project_id}",
        headers={**HOOK, "content-type": "application/json"},
        content=b'{"x": "' + b"a" * 300_000 + b'"}',
    )
    assert big.status_code == 413


async def test_a_daemon_that_is_shutting_down_releases_every_held_hook(rig: Rig) -> None:
    """uvicorn waits for in-flight requests when it shuts down, and a held hook is one that waits
    for a person, so Ctrl-C used to hang for the whole request window with a card pending."""
    held = [
        rig.start("PermissionRequest", tool_name="Bash", tool_input={"command": f"make hold{i}"})
        for i in range(2)
    ]
    async with asyncio.timeout(5):
        while len(await rig.cards()) < 2:
            await asyncio.sleep(0.02)
    rig.daemon.release_held_hooks()
    # Each hook is told there is no decision (so the terminal's dialog stays), and the cards end.
    assert [await asyncio.wait_for(task, 5) for task in held] == [{}, {}]
    assert await rig.cards() == []
    assert {entry["state"] for entry in await rig.history()} == {"abandoned"}


async def test_the_server_releases_held_hooks_before_it_waits_for_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uvicorn

    from cuttlefish.fleet.server import _FleetServer

    order: list[str] = []

    async def fake_shutdown(self: uvicorn.Server, sockets: Any = None) -> None:
        order.append("uvicorn waits")

    monkeypatch.setattr(uvicorn.Server, "shutdown", fake_shutdown)
    server = _FleetServer(uvicorn.Config(lambda *a: None), lambda: order.append("released"))
    await server.shutdown()
    assert order == ["released", "uvicorn waits"]
