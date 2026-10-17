"""What two ends say to each other while a device is being paired (ADR-0037, ADR-0039).

Pairing runs over the same encrypted session as everything else. The browser connects with the
one-time pairing secret as the psk and a new key, and its first message is a `PING` whose
payload is `{"name": ...}`. Until the owner confirms on the PC, the daemon keeps that session in
a waiting room where no request is served. Progress goes to the browser as `CONTROL` frames
(JSON, stream 0):

* `{"t": "pairing", "state": "waiting", "code": "123456"}`: seen, waiting for the owner
* `{"t": "pairing", "state": "paired", "device": id, "secret": base64url, "tier": "approve"}`
* `{"t": "pairing", "state": "rejected" | "expired" | "refused"}`
* `{"t": "revoked"}`: sent to a device just before its session is cut

The short code is derived from the handshake hash, which both ends hold and which binds both
static keys and both ephemeral keys. Someone who photographed the QR code and connects first
gets a different handshake and so a different code, and the owner, comparing codes, sees that
it is not the phone in their hand.
"""

from __future__ import annotations

import hashlib

CODE_DIGITS = 6
_CODE_DOMAIN = b"cuttlefish-pair-code/1"


def short_code(handshake_hash: bytes) -> str:
    """Six digits both screens show: `123456`."""
    digest = hashlib.sha256(_CODE_DOMAIN + handshake_hash).digest()
    number = int.from_bytes(digest[:4], "big") % 10**CODE_DIGITS
    return f"{number:0{CODE_DIGITS}d}"


def format_code(code: str) -> str:
    """`123456` as `123 456`, for people."""
    half = len(code) // 2
    return f"{code[:half]} {code[half:]}"
