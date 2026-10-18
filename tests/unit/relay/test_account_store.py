"""Unit (ADR-0043, CUT-135): the account store. Real SQLite in memory, nothing mocked."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from cuttlefish.relay import accounts
from cuttlefish.relay.accounts import MAX_ROOMS_PER_ACCOUNT, AccountError, AccountStore

SECRET = "o" * 40


@pytest.fixture
def store() -> AccountStore:
    s = AccountStore.open(":memory:")
    s.ensure_owner(SECRET)
    return s


def test_a_short_owner_secret_is_refused_with_a_way_to_make_one() -> None:
    s = AccountStore.open(":memory:")
    with pytest.raises(AccountError, match="at least 32 characters"):
        s.ensure_owner("short")


def test_the_schema_is_the_full_one_adr_0043_names(tmp_path: Path) -> None:
    path = tmp_path / "relay.db"
    AccountStore.open(path).close()
    names = {
        row[0]
        for row in sqlite3.connect(path).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"accounts", "identities", "sessions", "rooms", "usage_daily", "invites"} <= names
    assert path.stat().st_mode & 0o777 == 0o600


def test_nothing_secret_is_stored_in_the_clear(tmp_path: Path) -> None:
    path = tmp_path / "relay.db"
    s = AccountStore.open(path)
    s.ensure_owner(SECRET)
    session = s.sign_in_owner(SECRET)
    room, token = s.create_room(s.owner().id, "Work PC")  # type: ignore[union-attr]
    s.close()
    raw = b"".join(
        str(row).encode()
        for table in ("accounts", "identities", "sessions", "rooms")
        for row in sqlite3.connect(path).execute(f"SELECT * FROM {table}")
    )
    for secret in (SECRET, session, token):
        assert secret and secret.encode() not in raw
    assert room.id.encode() in raw


def test_the_owner_signs_in_with_the_secret_and_only_with_it(store: AccountStore) -> None:
    assert store.sign_in_owner("x" * 40) is None
    token = store.sign_in_owner(SECRET)
    assert token is not None
    account = store.account_for_session(token)
    assert account is not None and account.status == "active"


def test_changing_the_secret_on_the_server_rotates_it(store: AccountStore) -> None:
    owner = store.owner()
    store.ensure_owner("n" * 40)
    assert store.owner() == owner  # the same account, not a second one
    assert store.sign_in_owner(SECRET) is None
    assert store.sign_in_owner("n" * 40) is not None


def test_a_session_ends_on_sign_out_and_when_unknown(store: AccountStore) -> None:
    token = store.sign_in_owner(SECRET)
    assert store.account_for_session("not a session") is None
    assert store.account_for_session(None) is None
    store.sign_out(token)
    assert store.account_for_session(token) is None


def test_a_session_slides_while_used_and_stops_at_the_hard_limit(
    store: AccountStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = store.sign_in_owner(SECRET)
    start = time.time()
    day = 24 * 3600
    # Used every five days it keeps going, until 30 days after sign-in, then it ends.
    for n in range(1, 6):
        monkeypatch.setattr(accounts.time, "time", lambda n=n: start + n * 5 * day)
        assert store.account_for_session(token) is not None, n
    monkeypatch.setattr(accounts.time, "time", lambda: start + 31 * day)
    assert store.account_for_session(token) is None


def test_an_idle_session_expires(store: AccountStore, monkeypatch: pytest.MonkeyPatch) -> None:
    token = store.sign_in_owner(SECRET)
    start = time.time()
    monkeypatch.setattr(accounts.time, "time", lambda: start + 8 * 24 * 3600)
    assert store.account_for_session(token) is None


def test_a_room_is_made_with_a_token_that_works_once_stored_as_a_hash(store: AccountStore) -> None:
    owner = store.owner()
    assert owner is not None
    room, token = store.create_room(owner.id, "Work PC")
    assert len(room.id) >= 22 and len(token) >= 43  # 128 and 256 random bits
    assert store.authorise_daemon(room.id, token) == room
    assert store.authorise_daemon(room.id, token + "x") is None
    assert store.authorise_daemon(room.id, None) is None
    assert store.authorise_daemon("a-room-that-is-not-there", token) is None


def test_an_account_owns_a_limited_number_of_rooms(store: AccountStore) -> None:
    owner = store.owner()
    assert owner is not None
    for n in range(MAX_ROOMS_PER_ACCOUNT):
        store.create_room(owner.id, f"PC {n}")
    with pytest.raises(AccountError, match="delete one"):
        store.create_room(owner.id, "one too many")


@pytest.mark.parametrize("label", ["", "   ", "x" * 61])
def test_a_room_needs_a_sensible_label(store: AccountStore, label: str) -> None:
    owner = store.owner()
    assert owner is not None
    with pytest.raises(AccountError, match="label"):
        store.create_room(owner.id, label)


def test_rotating_a_token_ends_the_old_one(store: AccountStore) -> None:
    owner = store.owner()
    assert owner is not None
    room, old = store.create_room(owner.id, "Work PC")
    new = store.rotate_token(owner.id, room.id)
    assert store.authorise_daemon(room.id, old) is None
    assert store.authorise_daemon(room.id, new) is not None


def test_one_account_cannot_touch_anothers_room(store: AccountStore) -> None:
    owner = store.owner()
    assert owner is not None
    room, _ = store.create_room(owner.id, "Work PC")
    with pytest.raises(AccountError, match="no such room"):
        store.rotate_token("someone-else", room.id)
    assert store.delete_room("someone-else", room.id) is False
    assert store.rooms_of("someone-else") == []
    assert store.delete_room(owner.id, room.id) is True


def test_a_suspended_account_or_room_is_refused_with_the_same_answer(store: AccountStore) -> None:
    owner = store.owner()
    assert owner is not None
    room, token = store.create_room(owner.id, "Work PC")
    store._db.execute("UPDATE rooms SET status = 'suspended' WHERE id = ?", (room.id,))
    assert store.authorise_daemon(room.id, token) is None
    store._db.execute("UPDATE rooms SET status = 'active' WHERE id = ?", (room.id,))
    store._db.execute("UPDATE accounts SET status = 'suspended' WHERE id = ?", (owner.id,))
    assert store.authorise_daemon(room.id, token) is None
    assert store.sign_in_owner(SECRET) is None


def test_usage_adds_up_by_day_and_ignores_a_room_deleted_meanwhile(store: AccountStore) -> None:
    owner = store.owner()
    assert owner is not None
    room, _ = store.create_room(owner.id, "Work PC")
    store.add_usage(room.id, 100, 1)
    store.add_usage(room.id, 50, 2)
    assert store.usage_today(room.id) == (150, 3)
    store.delete_room(owner.id, room.id)
    store.add_usage(room.id, 10, 1)  # a daemon was still connected when its room was deleted
    assert store.usage_today(room.id) == (0, 0)
