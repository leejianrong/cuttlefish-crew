"""When a device counts as authenticated (ADR-0037).

Noise gives the daemon no proof that the caller holds the psk until the caller's first transport
message arrives and decrypts: a client with the wrong psk still completes the daemon's side of the
handshake (found in CUT-117). So the daemon's side is two steps. `accept` reads the first message
and returns a `PendingPeer`, whose `claimed_static` is only a claim. `PendingPeer.confirm` takes the
caller's first transport message and, only if it decrypts, returns an `EstablishedPeer`. Only that
type carries a `peer_static`, so code that needs an authenticated device cannot be handed an
unconfirmed one by mistake.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from cuttlefish.relay.protocol.noise import PROLOGUE, NoiseError, Responder, Transport


class UnknownPeerError(NoiseError):
    """No psk is held for the caller's static key (not paired, and no pairing is open)."""


@dataclass(slots=True)
class EstablishedPeer:
    """A caller that proved it holds the keys. `first_message` is the plaintext that proved it."""

    peer_static: bytes
    transport: Transport = field(repr=False)
    first_message: bytes


@dataclass(slots=True)
class PendingPeer:
    """A caller that has sent a first handshake message and has not yet proved anything."""

    claimed_static: bytes
    payload: bytes
    #: The daemon's handshake message to send back.
    reply: bytes
    _transport: Transport = field(repr=False)

    def confirm(self, first_message: bytes) -> EstablishedPeer:
        """Raises `NoiseError` if `first_message` does not decrypt; the peer is then no use."""
        plaintext = self._transport.decrypt(first_message)
        return EstablishedPeer(self.claimed_static, self._transport, plaintext)


def accept(
    static: X25519PrivateKey,
    psk_for: Callable[[bytes], bytes | None],
    message1: bytes,
    *,
    daemon_payload: bytes = b"",
    prologue: bytes = PROLOGUE,
    ephemeral: X25519PrivateKey | None = None,
) -> PendingPeer:
    """Read a caller's first handshake message and prepare the answer.

    `psk_for(claimed_static)` returns the 32-byte secret held for that key (a paired device's own,
    or the one-time
    pairing secret while a pairing is open), or `None`, which raises `UnknownPeerError` and sends
    nothing back.

    """
    responder = Responder(static, prologue=prologue, ephemeral=ephemeral)
    claimed_static, payload = responder.read_message1(message1)
    psk = psk_for(claimed_static)
    if psk is None:
        raise UnknownPeerError("no secret is held for this device")
    reply, transport = responder.write_message2(psk, daemon_payload)
    return PendingPeer(claimed_static, payload, reply, transport)
