"""How one daemon connection to the relay carries many browsers (ADR-0037).

A browser's websocket to the relay carries Noise messages as they are. The daemon has one websocket
to the relay for every browser in its room, so each binary message on it starts with a four-byte
client id the relay chose, and the rest is that browser's Noise message. The relay tells the daemon
who came and went with small text messages (`client-open`, `client-close`), and the daemon can ask
the relay to drop a browser with `close-client`. The relay sees ids, sizes and timing, never
content, because everything after the id is already encrypted.
"""

from __future__ import annotations

import json
import struct

#: The websocket close code a hosted relay answers a daemon with when its token is not accepted.
CLOSE_UNAUTHORISED = 4401
CLIENT_ID_LEN = 4
MAX_CLIENT_ID = 2**32 - 1

CLIENT_OPEN = "client-open"
CLIENT_CLOSE = "client-close"
CLOSE_CLIENT = "close-client"
_CONTROL_TYPES = {CLIENT_OPEN, CLIENT_CLOSE, CLOSE_CLIENT}


class EnvelopeError(ValueError):
    """A message the relay and the daemon could not both have meant."""


def wrap(client: int, payload: bytes) -> bytes:
    if not 0 < client <= MAX_CLIENT_ID:
        raise EnvelopeError("client id out of range")
    return struct.pack(">I", client) + payload


def unwrap(data: bytes) -> tuple[int, bytes]:
    if len(data) < CLIENT_ID_LEN:
        raise EnvelopeError("message shorter than a client id")
    (client,) = struct.unpack(">I", data[:CLIENT_ID_LEN])
    if client == 0:
        raise EnvelopeError("client id 0 is not a client")
    return client, data[CLIENT_ID_LEN:]


def control(kind: str, client: int, reason: str = "") -> str:
    if kind not in _CONTROL_TYPES:
        raise EnvelopeError(f"unknown control message {kind!r}")
    message: dict[str, object] = {"type": kind, "client": client}
    if reason:
        message["reason"] = reason
    return json.dumps(message, separators=(",", ":"))


def parse_control(text: str) -> tuple[str, int, str]:
    """`(type, client, reason)` of a control message, or `EnvelopeError`."""
    try:
        message = json.loads(text)
    except ValueError as exc:
        raise EnvelopeError("control message is not JSON") from exc
    if not isinstance(message, dict):
        raise EnvelopeError("control message is not an object")
    kind, client, reason = message.get("type"), message.get("client"), message.get("reason", "")
    if kind not in _CONTROL_TYPES or not isinstance(client, int) or isinstance(client, bool):
        raise EnvelopeError("malformed control message")
    return str(kind), client, reason if isinstance(reason, str) else ""
