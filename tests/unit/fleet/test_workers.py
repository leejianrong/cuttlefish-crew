"""Unit: worker machines (CUT-205, ADR-0057): how one is paired and kept, which are online, and the
join code a worker is given."""

from __future__ import annotations

import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from cuttlefish.fleet.devices import DeviceAuthority, DeviceError, DeviceStore, PairingManager
from cuttlefish.fleet.tiers import Tier
from cuttlefish.fleet.workers import WorkerLiveness
from cuttlefish.relay.connector import load_or_create_daemon_key, pairing_payload
from cuttlefish.relay.protocol import public_bytes
from cuttlefish.worker.link import Controller, JoinLinkError, parse_join_code
from cuttlefish.worker.run import Identity

KEY = bytes(range(32))


@pytest.fixture
def stores(tmp_path: Path) -> Iterator[tuple[DeviceStore, DeviceStore]]:
    devices = DeviceStore.open(tmp_path / "devices.db")
    workers = DeviceStore.open(tmp_path / "workers.db")
    yield devices, workers
    devices.close()
    workers.close()


async def test_a_worker_pairing_makes_a_worker_never_a_device(
    stores: tuple[DeviceStore, DeviceStore],
) -> None:
    devices, workers = stores
    pairing = PairingManager(devices, workers=workers)
    authority = DeviceAuthority(devices, pairing, workers)
    pairing.open_pairing("worker")
    pending = pairing.begin(KEY, "laptop", "123456")
    assert pending is not None and pending.kind == "worker"
    # Whatever tier the confirm names, a worker is at the worker tier.
    worker = pairing.confirm(pending.id, Tier.ADMIN)
    assert worker.tier is Tier.WORKER and worker.is_worker
    assert devices.list() == [] and [w.id for w in workers.list()] == [worker.id]
    assert authority.device_for(KEY) == worker.id and authority.secret_for(KEY) is not None


async def test_a_paired_worker_key_cannot_pair_again(
    stores: tuple[DeviceStore, DeviceStore],
) -> None:
    devices, workers = stores
    workers.create("laptop", KEY, b"s" * 32, Tier.WORKER)
    pairing = PairingManager(devices, workers=workers)
    pairing.open_pairing()
    assert pairing.begin(KEY, "same key", "000000") is None


def test_a_deleted_worker_is_forgotten_and_its_key_gets_no_secret(
    stores: tuple[DeviceStore, DeviceStore],
) -> None:
    devices, workers = stores
    worker = workers.create("laptop", KEY, b"s" * 32, Tier.WORKER)
    authority = DeviceAuthority(devices, PairingManager(devices, workers=workers), workers)
    workers.delete(worker.id)
    assert authority.secret_for(KEY) is None and authority.device_for(KEY) is None
    with pytest.raises(DeviceError):
        workers.delete(worker.id)


def test_a_worker_is_online_until_it_has_been_silent_too_long(
    stores: tuple[DeviceStore, DeviceStore],
) -> None:
    _devices, workers = stores
    now = [100.0]
    liveness = WorkerLiveness(offline_after=45, now=lambda: now[0])
    worker = workers.create("laptop", KEY, b"s" * 32, Tier.WORKER)
    assert not liveness.online(worker.id)  # a controller that just started knows nobody
    liveness.beat(worker.id, {"host": "box\x1b[31m", "platform": "Linux x86_64", "junk": 1})
    now[0] += 44
    assert liveness.online(worker.id)
    now[0] += 2
    assert not liveness.online(worker.id)
    shown = liveness.as_json(worker)
    assert shown["online"] is False and shown["kind"] == "worker" and shown["host"] == "box[31m"
    assert "secret" not in shown and "public_key" not in shown
    liveness.beat(worker.id)
    assert liveness.online(worker.id)
    assert liveness.as_json(worker)["host"] == "box[31m"  # a bare sign of life keeps what it said
    liveness.forget(worker.id)
    assert not liveness.online(worker.id)


def test_the_join_code_gives_the_controller_and_the_one_time_secret(tmp_path: Path) -> None:
    key = load_or_create_daemon_key(tmp_path / "key")
    code = pairing_payload("ws://relay.example", key, b"p" * 32, "room-1")
    controller, secret = parse_join_code(code)
    assert controller == Controller("ws://relay.example", "room-1", public_bytes(key))
    assert secret == b"p" * 32
    assert parse_join_code(f"https://box.ts.net/#pair={code}")[1] == secret


@pytest.mark.parametrize("text", ["", "not a code", "e30", "W10"])
def test_something_that_is_not_a_join_code_is_said_so(text: str) -> None:
    with pytest.raises(JoinLinkError, match="not a join code"):
        parse_join_code(text)


def test_a_worker_keeps_its_keys_in_one_private_file(tmp_path: Path) -> None:
    path = tmp_path / "home" / ".cuttlefish" / "worker.json"
    identity = Identity(Controller("ws://r", "room", KEY), b"k" * 32, b"s" * 32, "id", "pc")
    identity.save(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert Identity.load(path) == identity
    assert Identity.load(tmp_path / "missing.json") is None
