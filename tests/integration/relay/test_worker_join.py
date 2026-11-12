"""Integration (CUT-205, ADR-0057): a second machine joins with one command. A real relay, a real
connector, the fleet's own app and the real `cuttlefish.worker` client; the owner acts through the
app's routes with the loopback token."""

from __future__ import annotations

import asyncio
import json
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from relay_rig import Browser
from test_pairing_and_tiers import OWNER, Rig

from cuttlefish.relay.protocol import generate_static
from cuttlefish.worker import run as worker_run
from cuttlefish.worker.link import LinkError, WorkerLink, parse_join_code

FAST = {"beat_every": 0.1, "delays": (0.05,)}


async def _join_code(rig: Rig) -> str:
    made = await rig.post("/api/workers/pairing")
    assert made.status_code == 200
    return str(made.json()["payload"])


async def _pending_id(rig: Rig) -> str:
    for _ in range(600):
        pending = (await rig.owner.get("/api/devices", headers=OWNER)).json()["pending"]
        if pending:
            return str(pending[0]["id"])
        await asyncio.sleep(0.05)
    raise AssertionError("the worker never reached the waiting room")


async def _confirm_when_waiting(rig: Rig) -> dict[str, object]:
    confirmed = await rig.post(f"/api/devices/pending/{await _pending_id(rig)}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    return dict(confirmed.json())


async def _join(rig: Rig, tmp_path: Path, name: str = "laptop") -> tuple[worker_run.Identity, Path]:
    path = tmp_path / "worker-home" / ".cuttlefish" / "worker.json"
    lines: list[str] = []
    joining = asyncio.create_task(
        worker_run.join(await _join_code(rig), name=name, path=path, say=lines.append)
    )
    confirmed = await _confirm_when_waiting(rig)
    identity = await asyncio.wait_for(joining, 30)
    assert confirmed["kind"] == "worker" and confirmed["tier"] == "worker"
    assert lines and lines[0].startswith("Code ")  # the six digits the PC showed too
    return identity, path


async def _workers(rig: Rig) -> list[dict[str, object]]:
    return list((await rig.owner.get("/api/workers", headers=OWNER)).json()["workers"])


async def _until(check: Callable[[], Awaitable[bool]], seconds: float = 30.0) -> None:
    for _ in range(int(seconds / 0.05)):
        if await check():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("did not happen in time")


def _online(rig: Rig) -> Callable[[], Awaitable[bool]]:
    async def check() -> bool:
        return any(w["online"] for w in await _workers(rig))

    return check


async def test_a_worker_joins_after_the_owner_confirms_and_shows_online(
    rig: Rig,
    tmp_path: Path,
) -> None:
    identity, path = await _join(rig, tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert worker_run.Identity.load(path) == identity
    # A worker is not a device: it is in the worker registry only.
    assert (await rig.owner.get("/api/devices", headers=OWNER)).json()["devices"] == []
    runner = asyncio.create_task(worker_run.run(identity, path=path, say=lambda _t: None, **FAST))
    try:
        await _until(_online(rig))
        [worker] = await _workers(rig)
        assert worker["name"] == "laptop" and worker["id"] == identity.worker_id
        assert worker["host"]  # what it said about itself
        health = worker["health"]  # and how loaded it is, from the same heartbeat
        assert health["agents"] == 0 and health["disk_total"] > 0 and health["cpus"] >= 1
        assert isinstance(worker["flags"], list)
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)


async def test_a_worker_reports_what_it_has_and_the_controller_names_what_differs(
    rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CUT-214: this worker's profile lists only git; the controller's standard one lists more, so
    the dashboard's worker row names each tool the machine did not report."""
    own = tmp_path / "git-only.toml"
    own.write_text('version = 1\n[[tool]]\nname = "git"\napt = ["git"]\n')
    monkeypatch.setenv("CUTTLEFISH_WORKER_PROFILE", str(own))
    identity, path = await _join(rig, tmp_path)
    runner = asyncio.create_task(worker_run.run(identity, path=path, say=lambda _t: None, **FAST))
    try:
        await _until(_online(rig))
        [worker] = await _workers(rig)
        assert "git missing" not in worker["drift"]
        assert {"tmux missing", "docker missing", "none of kopicode, claude, codex"} <= set(
            worker["drift"]
        )
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)


async def test_a_worker_that_goes_silent_shows_offline(rig: Rig, tmp_path: Path) -> None:
    identity, path = await _join(rig, tmp_path)
    runner = asyncio.create_task(worker_run.run(identity, path=path, say=lambda _t: None, **FAST))
    await _until(_online(rig))
    runner.cancel()  # the network is gone: nothing more is heard
    await asyncio.gather(runner, return_exceptions=True)
    rig.link.liveness._offline_after = 0.3
    await asyncio.sleep(0.4)
    assert [w["online"] for w in await _workers(rig)] == [False]


async def test_an_unconfirmed_worker_is_served_nothing(rig: Rig) -> None:
    controller, secret = parse_join_code(await _join_code(rig))
    link = await WorkerLink.open(controller, generate_static(), secret, {"name": "stranger"})
    try:
        assert (await asyncio.wait_for(link.controls.get(), 30))["state"] == "waiting"
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(link.request("POST", "/api/worker/heartbeat", {}), 1)
    finally:
        await link.close()
    assert await _workers(rig) == []


async def test_the_owner_can_turn_a_worker_down(rig: Rig, tmp_path: Path) -> None:
    path = tmp_path / "w.json"
    joining = asyncio.create_task(
        worker_run.join(await _join_code(rig), name="vm", path=path, say=lambda _t: None)
    )
    await rig.post(f"/api/devices/pending/{await _pending_id(rig)}/reject")
    with pytest.raises(worker_run.JoinError, match="turned this machine down"):
        await asyncio.wait_for(joining, 30)
    assert not path.exists() and await _workers(rig) == []


async def test_a_join_code_works_once(rig: Rig, tmp_path: Path) -> None:
    code = await _join_code(rig)
    first = asyncio.create_task(
        worker_run.join(code, name="a", path=tmp_path / "a.json", say=lambda _t: None)
    )
    await _confirm_when_waiting(rig)
    await asyncio.wait_for(first, 30)
    with pytest.raises(worker_run.JoinError):
        await worker_run.join(code, name="b", path=tmp_path / "b.json", say=lambda _t: None)


async def test_a_worker_reaches_only_its_own_routes(rig: Rig, tmp_path: Path) -> None:
    identity, _path = await _join(rig, tmp_path)
    link = await WorkerLink.open(
        identity.controller, identity.static, identity.secret, {"name": "laptop"}
    )
    try:
        assert (await link.request("POST", "/api/worker/heartbeat", {}))[0] == 200
        for method, path in [
            ("GET", "/api/projects"),
            ("GET", f"/api/projects/{rig.project_id}"),
            ("GET", "/api/devices"),
            ("GET", "/api/workers"),
            ("GET", "/api/secrets"),
            ("POST", "/api/workers/pairing"),
            ("DELETE", f"/api/workers/{identity.worker_id}"),
            ("GET", "/api/never-added"),
        ]:
            status, _ = await link.request(method, path)
            assert status == 403, (method, path, status)
    finally:
        await link.close()


async def test_a_paired_device_cannot_send_a_heartbeat_or_become_a_worker(
    rig: Rig,
) -> None:
    device = await rig.pair(tier="view")
    browser: Browser = await rig.connect(device)
    try:
        status, _h, body = await browser.request("POST", "/api/worker/heartbeat", b"{}")
        assert status == 403 and "worker" in json.loads(body)["detail"]
    finally:
        await browser.close()
    refused = await rig.owner.patch(
        f"/api/devices/{device.id}", headers=OWNER, json={"tier": "worker"}
    )
    assert refused.status_code == 400


async def test_removing_a_worker_cuts_it_at_once_and_it_forgets_itself(
    rig: Rig,
    tmp_path: Path,
) -> None:
    identity, path = await _join(rig, tmp_path)
    lines: list[str] = []
    runner = asyncio.create_task(worker_run.run(identity, path=path, say=lines.append, **FAST))
    await _until(_online(rig))
    removed = await rig.owner.delete(f"/api/workers/{identity.worker_id}", headers=OWNER)
    assert removed.status_code == 200
    assert await asyncio.wait_for(runner, 30) == "removed"
    assert not path.exists()
    assert any("removed on the PC" in line for line in lines)
    assert await _workers(rig) == []
    # The next handshake fails: the registry no longer knows the key.
    with pytest.raises(LinkError):
        await WorkerLink.open(
            identity.controller, identity.static, identity.secret, {"name": "laptop"}
        )
    assert (await rig.owner.delete("/api/workers/nope", headers=OWNER)).status_code == 404
