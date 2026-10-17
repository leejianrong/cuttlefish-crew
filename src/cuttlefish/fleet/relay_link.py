"""The daemon's use of a relay (ADR-0037, CUT-119): the key, the pairing link, and the third
`SecurityCheck`.

`cuttlefish.relay.connector` knows nothing about the fleet. This module is the glue: it gives the
connector the daemon's own ASGI app and a token, and makes the app accept that token alongside the
loopback token or the password session (ADR-0011). The token is made per process and lives only in
memory, in the connector and in this check, so nothing a browser sends can be it.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path

from starlette.types import ASGIApp

from cuttlefish.fleet.auth import SecurityCheck
from cuttlefish.relay.connector import (
    RelayConnector,
    load_or_create_daemon_key,
    pairing_payload,
)


class RelayAwareCheck:
    """Accepts the connector's token, and defers to `primary` for anything else."""

    def __init__(self, primary: SecurityCheck, relay_token: str) -> None:
        self._primary = primary
        self._relay_token = relay_token.encode()

    def check(self, *, token: str | None, host: str | None, origin: str | None) -> None:
        if token and secrets.compare_digest(token.encode(), self._relay_token):
            return
        self._primary.check(token=token, host=host, origin=origin)


@dataclass(frozen=True, slots=True)
class RelaySettings:
    url: str
    key_path: Path


@dataclass(slots=True)
class RelayLink:
    """What `run_daemon` needs once a relay is configured."""

    settings: RelaySettings
    internal_token: str
    psk: bytes

    @classmethod
    def create(cls, settings: RelaySettings) -> RelayLink:
        return cls(settings, secrets.token_urlsafe(32), secrets.token_bytes(32))

    def wrap(self, primary: SecurityCheck) -> SecurityCheck:
        return RelayAwareCheck(primary, self.internal_token)

    def connector(self, app: ASGIApp) -> RelayConnector:
        return RelayConnector(
            app,
            relay_url=self.settings.url,
            static=load_or_create_daemon_key(self.settings.key_path),
            psk=self.psk,
            internal_token=self.internal_token,
        )

    def pairing_url(self, dashboard_url: str) -> str:
        """A dashboard address with the fragment that makes it use the relay."""
        key = load_or_create_daemon_key(self.settings.key_path)
        payload = pairing_payload(self.settings.url, key, self.psk)
        return f"{dashboard_url.rstrip('/')}/#relay={payload}"
