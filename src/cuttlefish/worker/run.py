"""`cuttlefish worker join` and `cuttlefish worker run` (ADR-0057).

A worker holds its own keypair, the secret the controller made for it at the confirm, and where the
controller is: one 0600 file, `~/.cuttlefish/worker.json`, and nothing else durable. `join` pairs
(the owner confirms the code on the PC) and saves it. `run` keeps the channel open, calls the
controller's heartbeat so the dashboard can say the machine is online, and reconnects with a growing
pause when the network drops. Removed on the PC, it forgets itself and stops.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import platform
import socket
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from cuttlefish.relay.protocol import format_code, generate_static, static_from_bytes
from cuttlefish.worker.link import Controller, LinkError, WorkerLink, parse_join_code, unb64

IDENTITY_FILE = "worker.json"
HEARTBEAT_EVERY_SECONDS = 15.0
RECONNECT_DELAYS = (1.0, 2.0, 5.0, 10.0, 30.0)
#: How long `join` waits for the owner before giving up (the controller's own waiting room is five
#: minutes).
JOIN_WAIT_SECONDS = 330.0


class JoinError(RuntimeError):
    """The worker was not let in; the message says why, for a person."""


def _say(text: str) -> None:
    print(text, flush=True)  # a service manager's log shows a line as it happens


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@dataclass(frozen=True, slots=True)
class Identity:
    controller: Controller
    key: bytes
    secret: bytes
    worker_id: str
    name: str

    @property
    def static(self) -> X25519PrivateKey:
        return static_from_bytes(self.key)

    def save(self, path: Path) -> None:
        body = {
            "relay": self.controller.relay,
            "room": self.controller.room,
            "daemon": _b64(self.controller.daemon),
            "key": _b64(self.key),
            "secret": _b64(self.secret),
            "id": self.worker_id,
            "name": self.name,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(body))
        path.chmod(0o600)

    @classmethod
    def load(cls, path: Path) -> Identity | None:
        try:
            body = json.loads(path.read_text())
            return cls(
                Controller(body["relay"], body["room"], unb64(body["daemon"])),
                unb64(body["key"]),
                unb64(body["secret"]),
                body["id"],
                body["name"],
            )
        except (OSError, ValueError, KeyError):
            return None


def identity_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".cuttlefish" / IDENTITY_FILE


def default_name() -> str:
    return socket.gethostname() or "worker"


async def join(
    code: str,
    *,
    name: str,
    path: Path,
    say: Callable[[str], None] = _say,
    wait_seconds: float = JOIN_WAIT_SECONDS,
) -> Identity:
    """Pair with the controller in `code`. Shows the short code the owner must match on the PC and
    returns once they confirm; raises `JoinError` if they turn it down or it times out."""
    controller, pairing_secret = parse_join_code(code)
    static = generate_static()
    try:
        link = await WorkerLink.open(controller, static, pairing_secret, {"name": name})
    except LinkError as exc:
        raise JoinError(str(exc)) from exc
    try:
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while True:
            left = deadline - asyncio.get_running_loop().time()
            try:
                message = await asyncio.wait_for(link.controls.get(), max(left, 0.01))
            except TimeoutError as exc:
                raise JoinError("nobody confirmed it in time; ask for a new command") from exc
            if message.get("t") != "pairing":
                continue
            state = message.get("state")
            if state == "waiting":
                say(f"Code {format_code(str(message['code']))}")
                say("Confirm on the PC (Workers on the dashboard) only if it shows the same code.")
            elif state == "paired":
                identity = Identity(
                    controller,
                    static.private_bytes_raw(),
                    unb64(message["secret"]),
                    str(message["device"]),
                    name,
                )
                identity.save(path)
                return identity
            else:
                raise JoinError(
                    {
                        "rejected": "the owner turned this machine down",
                        "expired": "nobody confirmed it in time; ask for a new command",
                        "refused": "that join code was used or has expired; ask for a new one",
                    }.get(str(state), f"the controller refused ({state})")
                )
    finally:
        await link.close()


def _about() -> dict[str, str]:
    return {"host": socket.gethostname(), "platform": f"{platform.system()} {platform.machine()}"}


async def run(
    identity: Identity,
    *,
    path: Path,
    say: Callable[[str], None] = _say,
    beat_every: float = HEARTBEAT_EVERY_SECONDS,
    delays: tuple[float, ...] = RECONNECT_DELAYS,
) -> str:
    """Stay connected and beat until cancelled. Returns `"removed"` once the owner removes this
    worker on the PC (its file is deleted: the controller no longer knows it)."""
    attempt = 0
    online = False
    while True:
        try:
            link = await WorkerLink.open(
                identity.controller, identity.static, identity.secret, {"name": identity.name}
            )
        except LinkError as exc:
            if online or attempt == 0:
                say(f"Offline: {exc}. Retrying.")
            online = False
            await asyncio.sleep(delays[min(attempt, len(delays) - 1)])
            attempt += 1
            continue
        try:
            while not link.closed.is_set():
                status, _ = await link.request("POST", "/api/worker/heartbeat", _about())
                if status != 200:
                    raise LinkError(f"the controller answered {status}")
                if not online:
                    say(f"Online as {identity.name}.")
                    online = True
                attempt = 0
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(link.closed.wait(), beat_every)
        except LinkError as exc:
            say(f"Lost the controller: {exc}")
            online = False
        finally:
            await link.close()
        while not link.controls.empty():
            if link.controls.get_nowait().get("t") == "revoked":
                path.unlink(missing_ok=True)
                say("This worker was removed on the PC. It has forgotten its keys.")
                return "removed"
        if online:
            say("Lost the controller. Retrying.")
            online = False
        await asyncio.sleep(delays[min(attempt, len(delays) - 1)])
        attempt += 1
