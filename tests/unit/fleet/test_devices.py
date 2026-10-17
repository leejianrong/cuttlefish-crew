"""Unit: the device store and the pairing manager (CUT-120, ADR-0039)."""

from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

import pytest

from cuttlefish.fleet.devices import (
    MAX_NAME_LENGTH,
    DeviceAuthority,
    DeviceError,
    DeviceStore,
    PairingManager,
    clean_name,
)
from cuttlefish.fleet.tiers import Tier

KEY = bytes(range(32))
OTHER = bytes(range(1, 33))


@pytest.fixture
def store(tmp_path: Path) -> DeviceStore:
    s = DeviceStore.open(tmp_path / "devices.db")
    yield s  # type: ignore[misc]
    s.close()


def test_the_file_is_private_to_its_owner(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "devices.db"
    DeviceStore.open(path).close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_device_is_kept_across_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "devices.db"
    first = DeviceStore.open(path)
    device = first.create("phone", KEY, b"s" * 32, Tier.APPROVE, now=100.0)
    first.close()
    second = DeviceStore.open(path)
    try:
        found = second.by_key(KEY)
        assert found is not None and found[0].id == device.id and found[1] == b"s" * 32
        assert found[0].tier is Tier.APPROVE and found[0].created_at == 100.0
    finally:
        second.close()


def test_a_key_can_belong_to_one_device_only(store: DeviceStore) -> None:
    store.create("a", KEY, b"s" * 32, Tier.VIEW)
    with pytest.raises(sqlite3.IntegrityError):
        store.create("b", KEY, b"t" * 32, Tier.VIEW)


def test_revoking_is_remembered_and_keeps_the_first_time(store: DeviceStore) -> None:
    device = store.create("a", KEY, b"s" * 32, Tier.APPROVE)
    first = store.revoke(device.id, now=50.0)
    again = store.revoke(device.id, now=99.0)
    assert not first.active and again.revoked_at == 50.0


def test_an_unknown_device_is_an_error_not_a_crash(store: DeviceStore) -> None:
    with pytest.raises(DeviceError):
        store.get("nope")
    with pytest.raises(DeviceError):
        store.set_tier("nope", Tier.ADMIN)
    assert store.find("nope") is None


def test_names_are_cut_down_to_something_safe_to_show() -> None:
    assert clean_name("  Jian's phone ") == "Jian's phone"
    assert clean_name("x" * 200) == "x" * MAX_NAME_LENGTH
    assert clean_name("a\x00b\x1b[31mred") == "ab[31mred"
    assert clean_name("") == clean_name(None) == clean_name(7) == "Unnamed device"


async def test_the_authority_answers_by_what_it_knows(store: DeviceStore) -> None:
    pairing = PairingManager(store)
    authority = DeviceAuthority(store, pairing)
    assert authority.secret_for(KEY) is None  # never seen, nothing open
    opened = pairing.open_pairing()
    assert authority.secret_for(KEY) == opened.secret and authority.device_for(KEY) is None
    pending = pairing.begin(KEY, "phone", "123456")
    assert pending is not None
    device = pairing.confirm(pending.id)
    secret = authority.secret_for(KEY)
    assert secret is not None and secret != opened.secret  # its own, from now on
    assert authority.device_for(KEY) == device.id
    store.revoke(device.id)
    assert authority.secret_for(KEY) is None and authority.device_for(KEY) is None


async def test_a_pairing_is_spent_by_the_first_device_and_by_nobody_after(
    store: DeviceStore,
) -> None:
    pairing = PairingManager(store)
    pairing.open_pairing()
    assert pairing.begin(KEY, "first", "111111") is not None
    assert pairing.current() is None
    assert pairing.begin(OTHER, "second", "222222") is None


async def test_a_second_pairing_replaces_the_first(store: DeviceStore) -> None:
    pairing = PairingManager(store)
    first = pairing.open_pairing()
    second = pairing.open_pairing()
    assert first.secret != second.secret and pairing.current() is second


async def test_a_pairing_expires_and_so_does_a_wait(store: DeviceStore) -> None:
    now = [1000.0]
    pairing = PairingManager(store, pairing_ttl=60, pending_ttl=30, now=lambda: now[0])
    pairing.open_pairing()
    now[0] += 61
    assert pairing.current() is None and pairing.begin(KEY, "late", "000000") is None
    pairing.open_pairing()
    pending = pairing.begin(KEY, "phone", "123456")
    assert pending is not None and len(pairing.pending()) == 1
    now[0] += 31
    assert pairing.pending() == []
    assert (await pending.decision()).state == "expired"
    with pytest.raises(DeviceError):
        pairing.confirm(pending.id)


async def test_the_waiting_room_is_bounded(store: DeviceStore) -> None:
    pairing = PairingManager(store)
    for i in range(8):
        pairing.open_pairing()
        assert pairing.begin(bytes([i]) * 32, f"d{i}", "000000") is not None
    pairing.open_pairing()
    assert pairing.begin(OTHER, "one too many", "000000") is None


async def test_a_key_that_is_already_paired_cannot_pair_again(store: DeviceStore) -> None:
    store.create("old", KEY, b"s" * 32, Tier.APPROVE)
    pairing = PairingManager(store)
    pairing.open_pairing()
    assert pairing.begin(KEY, "same key", "000000") is None


async def test_rejecting_tells_the_device_and_stores_nothing(store: DeviceStore) -> None:
    pairing = PairingManager(store)
    pairing.open_pairing()
    pending = pairing.begin(KEY, "phone", "123456")
    assert pending is not None
    pairing.reject(pending.id)
    assert (await pending.decision()).state == "rejected" and store.list() == []
