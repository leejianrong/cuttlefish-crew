"""Paired devices and the one-time pairing (ADR-0037, ADR-0039).

`DeviceStore` is a small SQLite file in the daemon's home (mode 0600): for each device its
public key, a name, a per-device secret (the psk for every later handshake), its tier, and when
it was made, last seen and revoked. A device is only ever created by `PairingManager.confirm`,
after the owner has seen it and said yes. `PairingManager` holds at most one open pairing (one
secret, five minutes, spent by the first valid first message) and the devices waiting in the
waiting room for the owner's decision.

`DeviceAuthority` is what the relay connector asks (it knows nothing about this module): which
secret to use for a key, whether a key belongs to an active device, and where to put a new
device."""

from __future__ import annotations

import asyncio
import os
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cuttlefish.fleet.tiers import DEFAULT_TIER, Tier
from cuttlefish.relay.connector import PairingOutcome, PairingRequest

PAIRING_TTL_SECONDS = 5 * 60
#: How long a device may wait in the waiting room for the owner before it is turned away.
PENDING_TTL_SECONDS = 5 * 60
MAX_PENDING = 8
MAX_NAME_LENGTH = 40


class DeviceError(RuntimeError):
    """A device or pairing action that cannot be done (unknown id, already decided, expired)."""


@dataclass(frozen=True, slots=True)
class Device:
    id: str
    name: str
    public_key: bytes
    tier: Tier
    created_at: float
    last_seen_at: float | None
    revoked_at: float | None

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    def as_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "tier": self.tier.label,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
            "revoked_at": self.revoked_at,
        }


def clean_name(raw: object) -> str:
    """A device's own name for itself, made safe to show: printable, short, never empty."""
    text = raw if isinstance(raw, str) else ""
    text = "".join(ch for ch in text if ch.isprintable()).strip()[:MAX_NAME_LENGTH]
    return text or "Unnamed device"


class DeviceStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    @classmethod
    def open(cls, path: Path) -> DeviceStore:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
        db = sqlite3.connect(path, check_same_thread=False)
        db.execute(
            "CREATE TABLE IF NOT EXISTS devices ("
            " id TEXT PRIMARY KEY, name TEXT NOT NULL, public_key BLOB NOT NULL UNIQUE,"
            " secret BLOB NOT NULL, tier INTEGER NOT NULL, created_at REAL NOT NULL,"
            " last_seen_at REAL, revoked_at REAL)"
        )
        db.commit()
        return cls(db)

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _row(row: tuple[Any, ...]) -> Device:
        return Device(
            id=str(row[0]),
            name=str(row[1]),
            public_key=bytes(row[2]),
            tier=Tier(int(row[3])),
            created_at=float(row[4]),
            last_seen_at=None if row[5] is None else float(row[5]),
            revoked_at=None if row[6] is None else float(row[6]),
        )

    _COLUMNS = "id, name, public_key, tier, created_at, last_seen_at, revoked_at"

    def create(
        self, name: str, public_key: bytes, secret: bytes, tier: Tier, *, now: float | None = None
    ) -> Device:
        device_id = secrets.token_hex(8)
        self._db.execute(
            "INSERT INTO devices (id, name, public_key, secret, tier, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                device_id,
                name,
                public_key,
                secret,
                int(tier),
                now if now is not None else time.time(),
            ),
        )
        self._db.commit()
        return self.get(device_id)

    def get(self, device_id: str) -> Device:
        row = self._db.execute(
            f"SELECT {self._COLUMNS} FROM devices WHERE id = ?", (device_id,)
        ).fetchone()
        if row is None:
            raise DeviceError(f"no device {device_id!r}")
        return self._row(row)

    def find(self, device_id: str) -> Device | None:
        try:
            return self.get(device_id)
        except DeviceError:
            return None

    def by_key(self, public_key: bytes) -> tuple[Device, bytes] | None:
        row = self._db.execute(
            f"SELECT {self._COLUMNS}, secret FROM devices WHERE public_key = ?", (public_key,)
        ).fetchone()
        return None if row is None else (self._row(row[:7]), bytes(row[7]))

    def list(self) -> list[Device]:
        rows = self._db.execute(
            f"SELECT {self._COLUMNS} FROM devices ORDER BY created_at"
        ).fetchall()
        return [self._row(row) for row in rows]

    def set_tier(self, device_id: str, tier: Tier) -> Device:
        self.get(device_id)
        self._db.execute("UPDATE devices SET tier = ? WHERE id = ?", (int(tier), device_id))
        self._db.commit()
        return self.get(device_id)

    def revoke(self, device_id: str, *, now: float | None = None) -> Device:
        self.get(device_id)
        self._db.execute(
            "UPDATE devices SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?",
            (now if now is not None else time.time(), device_id),
        )
        self._db.commit()
        return self.get(device_id)

    def touch(self, device_id: str, *, now: float | None = None) -> None:
        self._db.execute(
            "UPDATE devices SET last_seen_at = ? WHERE id = ?",
            (now if now is not None else time.time(), device_id),
        )
        self._db.commit()


@dataclass(slots=True)
class OpenPairing:
    secret: bytes = field(repr=False)
    expires_at: float


@dataclass(slots=True)
class PendingDevice:
    """A device in the waiting room: it proved the pairing secret and is waiting for the owner."""

    id: str
    name: str
    public_key: bytes
    code: str
    requested_at: float
    _future: asyncio.Future[PairingOutcome] = field(repr=False)
    _ttl: float = PENDING_TTL_SECONDS

    async def decision(self) -> PairingOutcome:
        try:
            return await asyncio.wait_for(asyncio.shield(self._future), self._ttl)
        except TimeoutError:
            if not self._future.done():
                self._future.set_result(PairingOutcome("expired"))
            return await self._future

    def as_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "requested_at": self.requested_at,
        }


class PairingManager:
    def __init__(
        self,
        store: DeviceStore,
        *,
        pairing_ttl: float = PAIRING_TTL_SECONDS,
        pending_ttl: float = PENDING_TTL_SECONDS,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._pairing_ttl = pairing_ttl
        self._pending_ttl = pending_ttl
        self._now = now
        self._open: OpenPairing | None = None
        self._pending: dict[str, PendingDevice] = {}

    # -- the open pairing -----------------------------------------------------------------

    def open_pairing(self) -> OpenPairing:
        """Start a pairing, replacing any earlier one: there is one secret at a time."""
        self._open = OpenPairing(secrets.token_bytes(32), self._now() + self._pairing_ttl)
        return self._open

    def cancel(self) -> None:
        self._open = None

    def current(self) -> OpenPairing | None:
        if self._open is not None and self._open.expires_at <= self._now():
            self._open = None
        return self._open

    # -- the waiting room ------------------------------------------------------------------

    def begin(self, public_key: bytes, name: str, code: str) -> PendingDevice | None:
        """A device with the pairing secret has sent its first message. This spends the pairing,
        whatever the owner decides, so a secret works once. `None` if there is no live pairing
        or the waiting room is full."""
        if self.current() is None or len(self.pending()) >= MAX_PENDING:
            return None
        if self._store.by_key(public_key) is not None:
            return None
        self._open = None
        pending = PendingDevice(
            id=secrets.token_hex(6),
            name=clean_name(name),
            public_key=public_key,
            code=code,
            requested_at=self._now(),
            _future=asyncio.get_running_loop().create_future(),
            _ttl=self._pending_ttl,
        )
        self._pending[pending.id] = pending
        return pending

    def pending(self) -> list[PendingDevice]:
        for pending in list(self._pending.values()):
            if pending._future.done() or pending.requested_at + self._pending_ttl <= self._now():
                if not pending._future.done():
                    pending._future.set_result(PairingOutcome("expired"))
                del self._pending[pending.id]
        return list(self._pending.values())

    def _take(self, pending_id: str) -> PendingDevice:
        self.pending()
        pending = self._pending.pop(pending_id, None)
        if pending is None:
            raise DeviceError("that device is no longer waiting (it may have timed out)")
        return pending

    def confirm(self, pending_id: str, tier: Tier = DEFAULT_TIER) -> Device:
        """The owner said yes: store the device with a secret of its own and tell it."""
        pending = self._take(pending_id)
        secret = secrets.token_bytes(32)
        device = self._store.create(pending.name, pending.public_key, secret, tier)
        pending._future.set_result(PairingOutcome("paired", device.id, secret, tier.label))
        return device

    def reject(self, pending_id: str) -> None:
        pending = self._take(pending_id)
        pending._future.set_result(PairingOutcome("rejected"))


class DeviceAuthority:
    """What the relay connector asks, answered from the store and the pairing manager."""

    def __init__(self, store: DeviceStore, pairing: PairingManager) -> None:
        self._store = store
        self._pairing = pairing

    def secret_for(self, public_key: bytes) -> bytes | None:
        known = self._store.by_key(public_key)
        if known is not None:
            device, secret = known
            return secret if device.active else None
        open_pairing = self._pairing.current()
        return open_pairing.secret if open_pairing is not None else None

    def device_for(self, public_key: bytes) -> str | None:
        known = self._store.by_key(public_key)
        return known[0].id if known is not None and known[0].active else None

    def begin_pairing(self, public_key: bytes, name: str, code: str) -> PairingRequest | None:
        return self._pairing.begin(public_key, name, code)
