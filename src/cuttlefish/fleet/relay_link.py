"""The daemon's use of a relay (ADR-0037, ADR-0039): its key, the device store, the pairing, the
guard that enforces a device's tier, and the third `SecurityCheck`.

`cuttlefish.relay.connector` knows nothing about the fleet. This module is the glue. It gives the
connector the daemon's own ASGI app and a token, and makes the app accept that token alongside the
loopback token or the password session (ADR-0011). The token is made per process and lives only in
memory, in the connector and in this check, so nothing a browser sends can be it. The connector
adds which paired device a request came from, and `RelayGuard` holds that device to its tier.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import satay.control
from starlette.types import ASGIApp

from cuttlefish.fleet.auth import SecurityCheck
from cuttlefish.fleet.devices import Device, DeviceAuthority, DeviceStore, PairingManager
from cuttlefish.fleet.tiers import Tier, required_tier
from cuttlefish.fleet.workers import WorkerLiveness
from cuttlefish.relay.connector import (
    RelayConnector,
    load_or_create_daemon_key,
    pairing_payload,
    room_id_for,
)
from cuttlefish.relay.protocol import public_bytes
from cuttlefish.worker import profile as worker_profile

DEVICE_HEADER = "x-cuttlefish-device"
#: A device's last-seen time is written at most this often.
_TOUCH_EVERY_SECONDS = 60.0


class TierRefusal(satay.control.AuthError):
    """A paired device asked for something above its tier (403). A subclass so the terminal can
    close with its own code, and a refused origin (also a 403) keeps closing with 4401."""


class RelayAwareCheck:
    """Accepts the connector's token, and defers to `primary` for anything else."""

    def __init__(self, primary: SecurityCheck, relay_token: str) -> None:
        self._primary = primary
        self._relay_token = relay_token.encode()

    def check(self, *, token: str | None, host: str | None, origin: str | None) -> None:
        if token and secrets.compare_digest(token.encode(), self._relay_token):
            return
        self._primary.check(token=token, host=host, origin=origin)


class RelayGuard:
    """Holds a request that came through the relay to its device's tier. The daemon decides this,
    never the relay and never the browser."""

    def __init__(
        self, relay_token: str, store: DeviceStore, workers: DeviceStore | None = None
    ) -> None:
        self._relay_token = relay_token.encode()
        self._store = store
        self._workers = workers
        self._touched: dict[str, float] = {}

    def is_relay(self, token: str | None) -> bool:
        return bool(token) and secrets.compare_digest((token or "").encode(), self._relay_token)

    def _find(self, device_id: str) -> Device | None:
        found = self._store.find(device_id)
        return found or (self._workers.find(device_id) if self._workers is not None else None)

    def device_for(self, headers: Mapping[str, str]) -> Device | None:
        return self._find(headers[DEVICE_HEADER]) if headers.get(DEVICE_HEADER) else None

    def authorize(self, *, device_id: str | None, method: str, path: str) -> Device:
        """The device, if it may make this request. Raises `AuthError`: 401 for a device that is
        unknown or revoked, 403 for one whose tier is too low."""
        device = self._find(device_id or "")
        if device is None or not device.active:
            raise satay.control.AuthError(401, "this device is not paired, or was revoked")
        needed = required_tier(method, path)
        # The worker tier is its own lane: a worker reaches only the routes listed for it (its
        # tier is below every other, so the order alone keeps it out of the rest), and a person's
        # device never reaches those (ADR-0057).
        if needed is Tier.WORKER and not device.is_worker:
            raise TierRefusal(403, "that route is for a worker machine")
        if needed is not None and device.tier < needed:
            raise TierRefusal(403, _too_low(device.tier, needed))
        now = time.monotonic()
        if now - self._touched.get(device.id, -_TOUCH_EVERY_SECONDS) >= _TOUCH_EVERY_SECONDS:
            self._touched[device.id] = now
            (self._workers if device.is_worker and self._workers else self._store).touch(device.id)
        return device


def _too_low(have: Tier, needed: Tier) -> str:
    return (
        f"This device is paired as {have.label} and this needs {needed.label}. "
        "Change it under Devices on the PC."
    )


@dataclass(frozen=True, slots=True)
class RelaySettings:
    url: str
    key_path: Path
    #: Where paired devices are kept; next to the key when not given.
    devices_path: Path | None = None
    #: Where paired worker machines are kept (ADR-0057); likewise.
    workers_path: Path | None = None
    #: The address a browser dials, when it differs from the daemon's own (`url`): behind
    #: `tailscale serve` the daemon reaches the relay on loopback `ws://` and a phone must use
    #: `wss://<machine>.<tailnet>.ts.net:<port>` (ADR-0041).
    public_url: str | None = None
    #: The address of the dashboard page a phone should open, when the daemon knows it (behind
    #: `tailscale serve` it is `https://<machine>.<tailnet>.ts.net`). The dashboard offers it as the
    #: address a pairing link opens, instead of whatever address the PC's own browser is on.
    page_url: str | None = None
    #: A hosted relay (ADR-0043) gives a room an id and a registration token; both come from the
    #: owner's account there. Without them the room is named from the daemon's key (open relay).
    room: str | None = None
    token: str | None = None

    @property
    def browser_url(self) -> str:
        return self.public_url or self.url


_LOG = logging.getLogger(__name__)


def _worker_profile() -> worker_profile.Profile | None:
    """The profile workers are compared with; an unreadable one is logged and drift is not shown."""
    try:
        return worker_profile.load()
    except worker_profile.ProfileError as exc:
        _LOG.warning("worker profile ignored, so drift is not shown: %s", exc)
        return None


class RelayLink:
    """What `run_daemon` and `create_app` need once a relay is configured."""

    def __init__(
        self,
        settings: RelaySettings,
        store: DeviceStore | None = None,
        workers: DeviceStore | None = None,
    ) -> None:
        self.settings = settings
        self.internal_token = secrets.token_urlsafe(32)
        self.store = store or DeviceStore.open(
            settings.devices_path or settings.key_path.parent / "devices.db"
        )
        self.workers = workers or DeviceStore.open(
            settings.workers_path or settings.key_path.parent / "workers.db"
        )
        self.liveness = WorkerLiveness(profile=_worker_profile())
        self.pairing = PairingManager(self.store, workers=self.workers)
        self.authority = DeviceAuthority(self.store, self.pairing, self.workers)
        self.guard = RelayGuard(self.internal_token, self.store, self.workers)
        self._daemon_key = load_or_create_daemon_key(settings.key_path)
        self._connector: RelayConnector | None = None

    @classmethod
    def create(cls, settings: RelaySettings) -> RelayLink:
        return cls(settings)

    @property
    def room(self) -> str:
        return self.settings.room or room_id_for(public_bytes(self._daemon_key))

    def wrap(self, primary: SecurityCheck) -> SecurityCheck:
        return RelayAwareCheck(primary, self.internal_token)

    def connector(self, app: ASGIApp) -> RelayConnector:
        self._connector = RelayConnector(
            app,
            relay_url=self.settings.url,
            static=self._daemon_key,
            authority=self.authority,
            internal_token=self.internal_token,
            room=self.settings.room,
            token=self.settings.token,
        )
        return self._connector

    def start_pairing(self, kind: str = "device") -> tuple[str, float]:
        """Open a pairing (replacing any earlier one). The payload goes in a URL fragment (or, for
        a worker, on `cuttlefish worker join`'s command line)."""
        opened = self.pairing.open_pairing(kind)
        return pairing_payload(
            self.settings.browser_url, self._daemon_key, opened.secret, self.settings.room
        ), opened.expires_at

    async def revoke(self, device_id: str) -> Device:
        """Revoke a device, and cut its live sessions at once."""
        device = self.store.revoke(device_id)
        if self._connector is not None:
            await self._connector.revoke_device(device_id)
        return device

    async def remove_worker(self, worker_id: str) -> Device:
        """Remove a worker (ADR-0057): its registry row goes, its live channel is closed at once
        and the next handshake fails. (Its leases are revoked here once a worker holds any.)"""
        worker = self.workers.delete(worker_id)
        self.liveness.forget(worker_id)
        if self._connector is not None:
            await self._connector.revoke_device(worker_id)
        return worker

    def close(self) -> None:
        self.store.close()
        self.workers.close()
