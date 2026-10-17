"""`Noise_IKpsk2_25519_AESGCM_SHA256` for a browser (initiator) talking to a daemon (responder)
(ADR-0037).

Written from the Noise specification, revision 34, and checked against vectors made by an
implementation we did not write (`tests/fixtures/relay/vectors.json`). IK: the browser already knows
the daemon's static key (from the pairing code), so a session takes one round trip and both sides
prove their key. psk2: a 32-byte secret is mixed in at the end of the daemon's message, so a party
without it cannot finish the handshake. The daemon chooses that secret after reading the browser's
static key, which is what lets it hold a different secret per paired device.

This module is the cryptography only. It gives no trust to a peer: see `session` for when a device
counts as authenticated.
"""

from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PROTOCOL_NAME = b"Noise_IKpsk2_25519_AESGCM_SHA256"
#: Both ends mix this in, so a client of another version or product cannot complete a handshake.
PROLOGUE = b"cuttlefish-relay/1"

KEY_LEN = 32
TAG_LEN = 16
#: The Noise limit for one message, handshake or transport.
MAX_MESSAGE_LEN = 65535
#: Noise reserves the last nonce value, so a session ends before it rather than wrap.
_MAX_NONCE = 2**64 - 2

assert len(PROTOCOL_NAME) == 32  # equal to the hash length, so the initial hash is the name itself


class NoiseError(Exception):
    """A message that could not be verified, or a session that cannot go on."""


def generate_static() -> X25519PrivateKey:
    return X25519PrivateKey.generate()


def static_from_bytes(raw: bytes) -> X25519PrivateKey:
    return X25519PrivateKey.from_private_bytes(raw)


def public_bytes(private: X25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def _dh(private: X25519PrivateKey, public: bytes) -> bytes:
    try:
        return private.exchange(X25519PublicKey.from_public_bytes(public))
    except ValueError as exc:  # a point of low order gives an all-zero secret
        raise NoiseError("invalid public key") from exc


def _hkdf2(chaining_key: bytes, material: bytes) -> tuple[bytes, bytes]:
    temp = hmac.new(chaining_key, material, hashlib.sha256).digest()
    out1 = hmac.new(temp, b"\x01", hashlib.sha256).digest()
    out2 = hmac.new(temp, out1 + b"\x02", hashlib.sha256).digest()
    return out1, out2


def _hkdf3(chaining_key: bytes, material: bytes) -> tuple[bytes, bytes, bytes]:
    temp = hmac.new(chaining_key, material, hashlib.sha256).digest()
    out1 = hmac.new(temp, b"\x01", hashlib.sha256).digest()
    out2 = hmac.new(temp, out1 + b"\x02", hashlib.sha256).digest()
    out3 = hmac.new(temp, out2 + b"\x03", hashlib.sha256).digest()
    return out1, out2, out3


def _nonce(counter: int) -> bytes:
    return b"\x00" * 4 + struct.pack(">Q", counter)


@dataclass(slots=True)
class _Symmetric:
    """The Noise SymmetricState: a running hash, a chaining key and a cipher key."""

    h: bytes = PROTOCOL_NAME
    ck: bytes = PROTOCOL_NAME
    k: bytes | None = None
    n: int = 0

    def mix_hash(self, data: bytes) -> None:
        self.h = hashlib.sha256(self.h + data).digest()

    def mix_key(self, material: bytes) -> None:
        self.ck, self.k = _hkdf2(self.ck, material)
        self.n = 0

    def mix_key_and_hash(self, material: bytes) -> None:
        self.ck, temp_h, self.k = _hkdf3(self.ck, material)
        self.mix_hash(temp_h)
        self.n = 0

    def encrypt_and_hash(self, plaintext: bytes) -> bytes:
        if self.k is None:
            self.mix_hash(plaintext)
            return plaintext
        cipher = AESGCM(self.k).encrypt(_nonce(self.n), plaintext, self.h)
        self.n += 1
        self.mix_hash(cipher)
        return cipher

    def decrypt_and_hash(self, cipher: bytes) -> bytes:
        if self.k is None:
            self.mix_hash(cipher)
            return cipher
        try:
            plain = AESGCM(self.k).decrypt(_nonce(self.n), cipher, self.h)
        except InvalidTag as exc:
            raise NoiseError("handshake message failed verification") from exc
        self.n += 1
        self.mix_hash(cipher)
        return plain

    def split(self) -> tuple[bytes, bytes]:
        return _hkdf2(self.ck, b"")


@dataclass(slots=True)
class Transport:
    """One direction's keys for the session after the handshake. A message that fails
    verification ends the session:
    the transport is ordered, so there is nothing to retry.
    """

    send_key: bytes = field(repr=False)
    recv_key: bytes = field(repr=False)
    handshake_hash: bytes = field(repr=False)
    _sent: int = 0
    _received: int = 0
    _broken: bool = False

    def encrypt(self, plaintext: bytes) -> bytes:
        if self._broken:
            raise NoiseError("the session has ended")
        if len(plaintext) + TAG_LEN > MAX_MESSAGE_LEN:
            raise NoiseError("message too large for one frame")
        if self._sent > _MAX_NONCE:
            raise NoiseError("the session has sent as many messages as its key allows")
        cipher = AESGCM(self.send_key).encrypt(_nonce(self._sent), plaintext, b"")
        self._sent += 1
        return cipher

    def decrypt(self, cipher: bytes) -> bytes:
        if self._broken:
            raise NoiseError("the session has ended")
        if self._received > _MAX_NONCE:
            self._broken = True
            raise NoiseError("the session has received as many messages as its key allows")
        try:
            plain = AESGCM(self.recv_key).decrypt(_nonce(self._received), cipher, b"")
        except InvalidTag as exc:
            self._broken = True
            raise NoiseError("message failed verification, the session has ended") from exc
        self._received += 1
        return plain


class Initiator:
    """The browser's side, in Python for tests and for any native client. One use per handshake."""

    def __init__(
        self,
        static: X25519PrivateKey,
        remote_static: bytes,
        psk: bytes,
        *,
        prologue: bytes = PROLOGUE,
        ephemeral: X25519PrivateKey | None = None,
    ) -> None:
        if len(psk) != KEY_LEN:
            raise NoiseError("the psk must be 32 bytes")
        self._static = static
        self._remote_static = remote_static
        self._psk = psk
        self._ephemeral = ephemeral or X25519PrivateKey.generate()
        self._sym = _Symmetric()
        self._sym.mix_hash(prologue)
        self._sym.mix_hash(remote_static)
        self._written = False

    def write_message1(self, payload: bytes = b"") -> bytes:
        if self._written:
            raise NoiseError("message 1 was already written")
        self._written = True
        sym = self._sym
        e_pub = public_bytes(self._ephemeral)
        sym.mix_hash(e_pub)
        sym.mix_key(e_pub)
        sym.mix_key(_dh(self._ephemeral, self._remote_static))
        encrypted_static = sym.encrypt_and_hash(public_bytes(self._static))
        sym.mix_key(_dh(self._static, self._remote_static))
        message = e_pub + encrypted_static + sym.encrypt_and_hash(payload)
        if len(message) > MAX_MESSAGE_LEN:
            raise NoiseError("handshake payload too large")
        return message

    def read_message2(self, message: bytes) -> tuple[Transport, bytes]:
        if not self._written:
            raise NoiseError("message 1 has not been written")
        if len(message) < KEY_LEN + TAG_LEN or len(message) > MAX_MESSAGE_LEN:
            raise NoiseError("malformed handshake message")
        sym = self._sym
        remote_ephemeral = message[:KEY_LEN]
        sym.mix_hash(remote_ephemeral)
        sym.mix_key(remote_ephemeral)
        sym.mix_key(_dh(self._ephemeral, remote_ephemeral))
        sym.mix_key(_dh(self._static, remote_ephemeral))
        sym.mix_key_and_hash(self._psk)
        payload = sym.decrypt_and_hash(message[KEY_LEN:])
        first, second = sym.split()
        return Transport(first, second, sym.h), payload


class Responder:
    """The daemon's side. `read_message1` reveals who is claiming to be calling; nothing about
    that claim is trusted
    until the caller has decrypted a first transport message (see `session`).
    """

    def __init__(
        self,
        static: X25519PrivateKey,
        *,
        prologue: bytes = PROLOGUE,
        ephemeral: X25519PrivateKey | None = None,
    ) -> None:
        self._static = static
        self._ephemeral = ephemeral or X25519PrivateKey.generate()
        self._sym = _Symmetric()
        self._sym.mix_hash(prologue)
        self._sym.mix_hash(public_bytes(static))
        self._remote_ephemeral: bytes | None = None
        self._remote_static: bytes | None = None

    def read_message1(self, message: bytes) -> tuple[bytes, bytes]:
        """The caller's claimed static key and its payload. Raises `NoiseError` if malformed."""
        if self._remote_static is not None:
            raise NoiseError("message 1 was already read")
        if len(message) < KEY_LEN + KEY_LEN + TAG_LEN + TAG_LEN or len(message) > MAX_MESSAGE_LEN:
            raise NoiseError("malformed handshake message")
        sym = self._sym
        remote_ephemeral = message[:KEY_LEN]
        sym.mix_hash(remote_ephemeral)
        sym.mix_key(remote_ephemeral)
        sym.mix_key(_dh(self._static, remote_ephemeral))
        remote_static = sym.decrypt_and_hash(message[KEY_LEN : KEY_LEN + KEY_LEN + TAG_LEN])
        sym.mix_key(_dh(self._static, remote_static))
        payload = sym.decrypt_and_hash(message[KEY_LEN + KEY_LEN + TAG_LEN :])
        self._remote_ephemeral = remote_ephemeral
        self._remote_static = remote_static
        return remote_static, payload

    def write_message2(self, psk: bytes, payload: bytes = b"") -> tuple[bytes, Transport]:
        """Answer with `psk`, chosen after `read_message1` so it can be that caller's own."""
        if self._remote_ephemeral is None or self._remote_static is None:
            raise NoiseError("message 1 has not been read")
        if len(psk) != KEY_LEN:
            raise NoiseError("the psk must be 32 bytes")
        sym = self._sym
        e_pub = public_bytes(self._ephemeral)
        sym.mix_hash(e_pub)
        sym.mix_key(e_pub)
        sym.mix_key(_dh(self._ephemeral, self._remote_ephemeral))
        sym.mix_key(_dh(self._ephemeral, self._remote_static))
        sym.mix_key_and_hash(psk)
        message = e_pub + sym.encrypt_and_hash(payload)
        if len(message) > MAX_MESSAGE_LEN:
            raise NoiseError("handshake payload too large")
        first, second = sym.split()
        return message, Transport(second, first, sym.h)
