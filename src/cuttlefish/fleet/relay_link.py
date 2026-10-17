"""The daemon's use of a relay (ADR-0037, ADR-0039): its key, the device store, the pairing, the
guard that enforces a device's tier, and the third `SecurityCheck`.

`cuttlefish.relay.connector` knows nothing about the fleet. This module is the glue. It gives the
connector the daemon's own ASGI app and a token, and makes the app accept that token alongside the
loopback token or the password session (ADR-0011). The token is made per process and lives only in
memory, in the connector and in this check, so nothing a browser sends can be it. The connector
adds which paired device a request came from, and `RelayGuard` holds that device to its tier.
"""

from __future__ import annotations

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
from cuttlefish.relay.connector import (
    RelayConnector,
    load_or_create_daemon_key,
    pairing_payload,
    room_id_for,
)
from cuttlefish.relay.protocol import public_bytes

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

    def __init__(self, relay_token: str, store: DeviceStore) -> None:
        self._relay_token = relay_token.encode()
        self._store = store
        self._touched: dict[str, float] = {}

    def is_relay(self, token: str | None) -> bool:
        return bool(token) and secrets.compare_digest((token or "").encode(), self._relay_token)

    def device_for(self, headers: Mapping[str, str]) -> Device | None:
        return (
            self._store.find(headers.get(DEVICE_HEADER, "")) if headers.get(DEVICE_HEADER) else None
        )

    def authorize(self, *, device_id: str | None, method: str, path: str) -> Device:
        """The device, if it may make this request. Raises `AuthError`: 401 for a device that is
        unknown or revoked, 403 for one whose tier is too low."""
        device = self._store.find(device_id or "")
        if device is None or not device.active:
            raise satay.control.AuthError(401, "this device is not paired, or was revoked")
        needed = required_tier(method, path)
        if needed is not None and device.tier < needed:
            raise TierRefusal(403, _too_low(device.tier, needed))
        now = time.monotonic()
        if now - self._touched.get(device.id, -_TOUCH_EVERY_SECONDS) >= _TOUCH_EVERY_SECONDS:
            self._touched[device.id] = now
            self._store.touch(device.id)
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


class RelayLink:
    """What `run_daemon` and `create_app` need once a relay is configured."""

    def __init__(self, settings: RelaySettings, store: DeviceStore | None = None) -> None:
        self.settings = settings
        self.internal_token = secrets.token_urlsafe(32)
        self.store = store or DeviceStore.open(
            settings.devices_path or settings.key_path.parent / "devices.db"
        )
        self.pairing = PairingManager(self.store)
        self.authority = DeviceAuthority(self.store, self.pairing)
        self.guard = RelayGuard(self.internal_token, self.store)
        self._daemon_key = load_or_create_daemon_key(settings.key_path)
        self._connector: RelayConnector | None = None

    @classmethod
    def create(cls, settings: RelaySettings) -> RelayLink:
        return cls(settings)

    @property
    def room(self) -> str:
        return room_id_for(public_bytes(self._daemon_key))

    def wrap(self, primary: SecurityCheck) -> SecurityCheck:
        return RelayAwareCheck(primary, self.internal_token)

    def connector(self, app: ASGIApp) -> RelayConnector:
        self._connector = RelayConnector(
            app,
            relay_url=self.settings.url,
            static=self._daemon_key,
            authority=self.authority,
            internal_token=self.internal_token,
        )
        return self._connector

    def start_pairing(self) -> tuple[str, float]:
        """Open a pairing (replacing any earlier one). The payload goes in a URL fragment."""
        opened = self.pairing.open_pairing()
        return pairing_payload(
            self.settings.url, self._daemon_key, opened.secret
        ), opened.expires_at

    async def revoke(self, device_id: str) -> Device:
        """Revoke a device, and cut its live sessions at once."""
        device = self.store.revoke(device_id)
        if self._connector is not None:
            await self._connector.revoke_device(device_id)
        return device

    def close(self) -> None:
        self.store.close()
