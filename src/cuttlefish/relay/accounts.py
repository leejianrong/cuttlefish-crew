"""Accounts, sessions and rooms for the hosted relay (ADR-0043, CUT-135).

A SQLite file holds who an account is, which rooms it owns and how many bytes each moved. It holds
no project data, model keys or agent credentials, and no IP address. Every secret in it (the owner's
sign-in secret, a session, a room's registration token) is 256 random bits or more, so a plain
SHA-256 of it is enough: there is nothing to brute-force, and a leak of the file lets nobody in.

The schema is the full one ADR-0043 names, so the later cards (GitHub and Google sign-in, quotas,
the abuse switch) add behaviour and do not alter tables. Only the `owner` provider is wired up so
far.

This module must not import the fleet, secrets or episodic code
(`tests/unit/relay/test_boundary.py`).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1

#: A session slides while it is used (idle limit) and never lasts past the hard limit.
SESSION_IDLE_SECONDS = 7 * 24 * 3600
SESSION_HARD_SECONDS = 30 * 24 * 3600

MAX_ROOMS_PER_ACCOUNT = 5
MIN_OWNER_SECRET_LENGTH = 32
MAX_LABEL_LENGTH = 60

OWNER_PROVIDER = "owner"
OWNER_SUBJECT = "owner"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    created REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'deleted')),
    email TEXT,
    display_name TEXT
);
CREATE TABLE IF NOT EXISTS identities (
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    subject TEXT NOT NULL,
    created REAL NOT NULL,
    secret_hash TEXT,
    PRIMARY KEY (provider, subject)
);
CREATE TABLE IF NOT EXISTS sessions (
    hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    created REAL NOT NULL,
    last_used REAL NOT NULL,
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS rooms (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    created REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    token_hash TEXT NOT NULL,
    last_online REAL
);
CREATE INDEX IF NOT EXISTS rooms_by_account ON rooms(account_id);
CREATE TABLE IF NOT EXISTS usage_daily (
    room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    bytes INTEGER NOT NULL DEFAULT 0,
    connections INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (room_id, day)
);
CREATE TABLE IF NOT EXISTS invites (
    email TEXT PRIMARY KEY,
    created REAL NOT NULL,
    used REAL
);
"""


class AccountError(Exception):
    """A request the store refuses, with a sentence a person can act on."""


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    status: str
    email: str | None
    display_name: str | None


@dataclass(frozen=True, slots=True)
class Room:
    id: str
    account_id: str
    label: str
    created: float
    status: str
    last_online: float | None


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


class AccountStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    @classmethod
    def open(cls, path: Path | str) -> AccountStore:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # The relay is one asyncio process; handlers call this on the event loop's thread.
        connection = sqlite3.connect(str(path), isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(_SCHEMA)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        if str(path) != ":memory:":
            Path(path).chmod(0o600)
        return cls(connection)

    def close(self) -> None:
        self._db.close()

    # -- the owner --------------------------------------------------------------------------

    def ensure_owner(self, secret: str) -> Account:
        """Make the one owner account if it is missing, and set its sign-in secret to `secret`
        (changing the secret on the server is how an operator rotates it)."""
        if len(secret) < MIN_OWNER_SECRET_LENGTH:
            raise AccountError(
                f"the owner secret must be at least {MIN_OWNER_SECRET_LENGTH} characters; "
                "make one with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )
        row = self._db.execute(
            "SELECT account_id FROM identities WHERE provider = ? AND subject = ?",
            (OWNER_PROVIDER, OWNER_SUBJECT),
        ).fetchone()
        if row is None:
            account_id = secrets.token_hex(16)
            now = time.time()
            self._db.execute(
                "INSERT INTO accounts (id, created, display_name) VALUES (?, ?, 'Owner')",
                (account_id, now),
            )
            self._db.execute(
                "INSERT INTO identities (account_id, provider, subject, created, secret_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (account_id, OWNER_PROVIDER, OWNER_SUBJECT, now, _digest(secret)),
            )
        else:
            account_id = row["account_id"]
            self._db.execute(
                "UPDATE identities SET secret_hash = ? WHERE provider = ? AND subject = ?",
                (_digest(secret), OWNER_PROVIDER, OWNER_SUBJECT),
            )
        account = self.account(account_id)
        assert account is not None
        return account

    def owner(self) -> Account | None:
        row = self._db.execute(
            "SELECT account_id FROM identities WHERE provider = ? AND subject = ?",
            (OWNER_PROVIDER, OWNER_SUBJECT),
        ).fetchone()
        return self.account(row["account_id"]) if row is not None else None

    def sign_in_owner(self, secret: str) -> str | None:
        """A session token for the owner if `secret` is right and the account is active."""
        row = self._db.execute(
            "SELECT i.account_id, i.secret_hash, a.status FROM identities i "
            "JOIN accounts a ON a.id = i.account_id WHERE i.provider = ? AND i.subject = ?",
            (OWNER_PROVIDER, OWNER_SUBJECT),
        ).fetchone()
        # Compare even when there is no owner, so the time taken does not say whether one exists.
        stored = row["secret_hash"] if row is not None else _digest("no owner")
        right = _same(_digest(secret), stored)
        if row is None or not right or row["status"] != "active":
            return None
        return self._new_session(row["account_id"])

    # -- sessions ---------------------------------------------------------------------------

    def _new_session(self, account_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        self._db.execute(
            "INSERT INTO sessions (hash, account_id, created, last_used, expires) "
            "VALUES (?, ?, ?, ?, ?)",
            (_digest(token), account_id, now, now, now + SESSION_IDLE_SECONDS),
        )
        return token

    def account_for_session(self, token: str | None) -> Account | None:
        """The account a session belongs to, sliding its idle limit. None if unknown, expired,
        or the account is not active."""
        if not token:
            return None
        key = _digest(token)
        row = self._db.execute("SELECT * FROM sessions WHERE hash = ?", (key,)).fetchone()
        if row is None:
            return None
        now = time.time()
        if row["expires"] <= now:
            self._db.execute("DELETE FROM sessions WHERE hash = ?", (key,))
            return None
        account = self.account(row["account_id"])
        if account is None or account.status != "active":
            return None
        expires = min(row["created"] + SESSION_HARD_SECONDS, now + SESSION_IDLE_SECONDS)
        self._db.execute(
            "UPDATE sessions SET last_used = ?, expires = ? WHERE hash = ?", (now, expires, key)
        )
        return account

    def sign_out(self, token: str | None) -> None:
        if token:
            self._db.execute("DELETE FROM sessions WHERE hash = ?", (_digest(token),))

    def account(self, account_id: str) -> Account | None:
        row = self._db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            return None
        return Account(row["id"], row["status"], row["email"], row["display_name"])

    # -- rooms ------------------------------------------------------------------------------

    def create_room(self, account_id: str, label: str) -> tuple[Room, str]:
        """A new room for an account, and its registration token. The token is returned once and
        only its hash is kept."""
        label = label.strip()
        if not label or len(label) > MAX_LABEL_LENGTH:
            raise AccountError(f"give the room a label of 1 to {MAX_LABEL_LENGTH} characters")
        count = self._db.execute(
            "SELECT COUNT(*) FROM rooms WHERE account_id = ?", (account_id,)
        ).fetchone()[0]
        if count >= MAX_ROOMS_PER_ACCOUNT:
            raise AccountError(
                f"an account can own {MAX_ROOMS_PER_ACCOUNT} rooms; delete one to make another"
            )
        room_id = secrets.token_urlsafe(16)
        token = secrets.token_urlsafe(32)
        now = time.time()
        self._db.execute(
            "INSERT INTO rooms (id, account_id, label, created, token_hash) VALUES (?, ?, ?, ?, ?)",
            (room_id, account_id, label, now, _digest(token)),
        )
        room = self.room(room_id)
        assert room is not None
        return room, token

    def room(self, room_id: str) -> Room | None:
        row = self._db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        return _room(row) if row is not None else None

    def rooms_of(self, account_id: str) -> list[Room]:
        rows = self._db.execute(
            "SELECT * FROM rooms WHERE account_id = ? ORDER BY created", (account_id,)
        ).fetchall()
        return [_room(row) for row in rows]

    def rotate_token(self, account_id: str, room_id: str) -> str:
        token = secrets.token_urlsafe(32)
        cursor = self._db.execute(
            "UPDATE rooms SET token_hash = ? WHERE id = ? AND account_id = ?",
            (_digest(token), room_id, account_id),
        )
        if cursor.rowcount == 0:
            raise AccountError("no such room")
        return token

    def delete_room(self, account_id: str, room_id: str) -> bool:
        cursor = self._db.execute(
            "DELETE FROM rooms WHERE id = ? AND account_id = ?", (room_id, account_id)
        )
        return cursor.rowcount > 0

    def authorise_daemon(self, room_id: str, token: str | None) -> Room | None:
        """The room a daemon may serve, if `token` is the room's registration token and both the
        room and its account are active. One answer for every way to fail."""
        row = self._db.execute(
            "SELECT r.*, a.status AS account_status FROM rooms r "
            "JOIN accounts a ON a.id = r.account_id WHERE r.id = ?",
            (room_id,),
        ).fetchone()
        stored = row["token_hash"] if row is not None else _digest("no room")
        right = _same(_digest(token or ""), stored)
        if row is None or not right:
            return None
        if row["status"] != "active" or row["account_status"] != "active":
            return None
        return _room(row)

    # -- usage ------------------------------------------------------------------------------

    def mark_online(self, room_id: str) -> None:
        self._db.execute("UPDATE rooms SET last_online = ? WHERE id = ?", (time.time(), room_id))

    def add_usage(self, room_id: str, relayed: int, connections: int) -> None:
        if relayed <= 0 and connections <= 0:
            return
        if self.room(room_id) is None:  # deleted while the daemon was still connected
            return
        self._db.execute(
            "INSERT INTO usage_daily (room_id, day, bytes, connections) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(room_id, day) DO UPDATE SET bytes = bytes + excluded.bytes, "
            "connections = connections + excluded.connections",
            (room_id, today(), relayed, connections),
        )

    def usage_today(self, room_id: str) -> tuple[int, int]:
        row = self._db.execute(
            "SELECT bytes, connections FROM usage_daily WHERE room_id = ? AND day = ?",
            (room_id, today()),
        ).fetchone()
        return (row["bytes"], row["connections"]) if row is not None else (0, 0)


def _room(row: sqlite3.Row) -> Room:
    return Room(
        row["id"],
        row["account_id"],
        row["label"],
        row["created"],
        row["status"],
        row["last_online"],
    )
