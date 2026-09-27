"""Real login/session auth for a non-loopback `cuttlefish serve` bind (ADR-0011,
KAN-1706).

`satay.control.SecurityPolicy`'s guard -- one shared bearer token, generated at
process start and printed once -- is deliberately *not* real network
authentication (satay's own `NonLoopbackBindError` says so outright); it is
proportionate to a loopback-only, browser-reachable local port, not to a socket
reachable from a LAN or the open internet. Binding non-loopback needs a real
credential check cuttlefish owns itself -- satay stays exactly as ADR-0014 built
it (ADR-0011's own "no changes to satay-runtime beyond a narrow ask" reasoning).

One operator, one password (`CUTTLEFISH_SERVE_PASSWORD`, an env var only -- never
a CLI flag, so it never lands in shell history or `ps`). `login` mints a
short-lived, HMAC-signed session token instead of handing back the password or a
second static secret; `check` verifies one the same shape
`satay.control.SecurityPolicy.check` verifies its own static token, so
`cuttlefish.fleet.server.create_app` can treat either guard identically through
the `SecurityCheck` protocol below.
"""

from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass, field
from typing import Protocol

import satay.control

#: A session is good for 12 hours -- long enough for a working session, short
#: enough that a leaked token (a log, a screen-share) is not a standing secret
#: the way the loopback-mode static token effectively is for the process's life.
DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60

#: A non-loopback bind is reachable by anyone who can guess the password; refuse
#: one short enough to guess in an afternoon rather than accept it silently.
MIN_PASSWORD_LENGTH = 12

#: Consecutive wrong passwords before a short lockout -- a cheap, real guard a
#: never-expiring static token had no equivalent for.
_LOCKOUT_THRESHOLD = 5
_LOCKOUT_SECONDS = 30.0


class SecurityCheck(Protocol):
    """Either guard `cuttlefish.fleet.server.create_app` can be built with --
    `satay.control.SecurityPolicy` (loopback, static token) or `SessionAuth`
    (non-loopback, password/session) -- the app's own middleware calls this and
    never needs to know which one it holds."""

    def check(self, *, token: str | None, host: str | None, origin: str | None) -> None: ...


class WeakPasswordError(ValueError):
    """`CUTTLEFISH_SERVE_PASSWORD` is shorter than `MIN_PASSWORD_LENGTH` -- refused
    at daemon startup, not silently accepted."""


@dataclass(slots=True)
class SessionAuth:
    """`SecurityCheck` for a non-loopback bind: one password, HMAC-signed session
    tokens minted by `login`, an in-memory lockout after repeated failures.

    `allowed_origins` is the operator's explicit `--allow-origin` list -- an
    empty set means only same-machine browser origins work (the loopback regex
    `create_app`'s own CORS middleware always keeps), not a silent wildcard.
    The signing secret is random per process start and never persisted:
    restarting the daemon invalidates every outstanding session at once, the
    same "restart to revoke" posture the loopback mode's static token already
    has.
    """

    password: str
    allowed_origins: frozenset[str] = frozenset()
    ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS
    _secret: bytes = field(default_factory=lambda: secrets.token_bytes(32), repr=False)
    _failures: int = field(default=0, repr=False)
    _locked_until: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        if len(self.password) < MIN_PASSWORD_LENGTH:
            raise WeakPasswordError(
                f"CUTTLEFISH_SERVE_PASSWORD must be at least {MIN_PASSWORD_LENGTH} "
                "characters -- a non-loopback bind is reachable by anyone who can "
                "guess it (ADR-0011)"
            )

    def login(self, password: str) -> str:
        """Verify `password` and mint a fresh session token, or raise
        `satay.control.AuthError` (401 on a wrong password, 429 while locked out)."""
        now = time.time()
        if now < self._locked_until:
            raise satay.control.AuthError(
                429, f"too many failed attempts; try again in {int(self._locked_until - now)}s"
            )
        if not secrets.compare_digest(password, self.password):
            self._failures += 1
            if self._failures >= _LOCKOUT_THRESHOLD:
                self._locked_until = now + _LOCKOUT_SECONDS
                self._failures = 0
            raise satay.control.AuthError(401, "invalid password")
        self._failures = 0
        return self._mint(now)

    def _mint(self, now: float) -> str:
        expiry = int(now + self.ttl_seconds)
        return f"{expiry}.{self._sign(expiry)}"

    def _sign(self, expiry: int) -> str:
        return hmac.new(self._secret, str(expiry).encode(), "sha256").hexdigest()

    def _verify_session_token(self, token: str) -> bool:
        expiry_str, sep, mac = token.partition(".")
        if not sep or not mac or not expiry_str.isdigit():
            return False
        if int(expiry_str) < time.time():
            return False
        return hmac.compare_digest(mac, self._sign(int(expiry_str)))

    def check(self, *, token: str | None, host: str | None, origin: str | None) -> None:
        """`host` is unchecked (see ADR-0011: a `Host`-based rebinding defence only
        means something when loopback is the whole trust boundary) -- the session
        token plus `allowed_origins` are the real guard here.

        A loopback `origin` (`http://localhost:5183`, Vite's own dev server; a
        curl from the operator's own machine) is allowed regardless of
        `allowed_origins`, mirroring `satay.control.SecurityPolicy.check`'s
        identical carve-out -- same-machine code is the one thing already
        trusted in *either* auth mode (verified live: without this, the
        dashboard's own dev server talking to a non-loopback daemon on
        `127.0.0.1` was rejected with no way to fix it short of adding every
        local dev port to `--allow-origin`)."""
        del host
        if not token or not self._verify_session_token(token):
            raise satay.control.AuthError(401, "missing or invalid session token")
        if (
            origin is not None
            and origin not in self.allowed_origins
            and not satay.control.is_loopback_host(origin)
        ):
            raise satay.control.AuthError(403, f"disallowed Origin {origin!r}")


__all__ = [
    "DEFAULT_SESSION_TTL_SECONDS",
    "MIN_PASSWORD_LENGTH",
    "SecurityCheck",
    "SessionAuth",
    "WeakPasswordError",
]
