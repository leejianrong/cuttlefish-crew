"""Regenerate `vectors.json` with an implementation we did not write.

Run by hand, never in CI, from a scratch venv that has the `noiseprotocol` package (pure Python,
on PyPI):

    uv venv /tmp/noise-ref && uv pip install --python /tmp/noise-ref/bin/python noiseprotocol
    /tmp/noise-ref/bin/python tests/fixtures/relay/make_vectors.py

It plays one `Noise_IKpsk2_25519_AESGCM_SHA256` handshake with fixed keys and writes every byte.
The Python and TypeScript tests replay the file, so each side is checked against that library
rather than only against the other. The keys are throwaway test values.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from noise.connection import Keypair, NoiseConnection

NAME = b"Noise_IKpsk2_25519_AESGCM_SHA256"
PROLOGUE = b"cuttlefish-relay/1"
# Fixed, obviously fake private keys (32 bytes each).
S_I, E_I, S_R, E_R = (bytes([n]) * 32 for n in (0x11, 0x22, 0x33, 0x44))
PSK = bytes(range(32))
PAYLOAD1, PAYLOAD2 = b"hello from the browser", b"hello from the daemon"


def public(private: bytes) -> bytes:
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    key = X25519PrivateKey.from_private_bytes(private)
    return key.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)


def main() -> None:
    init = NoiseConnection.from_name(NAME)
    init.set_as_initiator()
    init.set_psks(PSK)
    init.set_prologue(PROLOGUE)
    init.set_keypair_from_private_bytes(Keypair.STATIC, S_I)
    init.set_keypair_from_private_bytes(Keypair.EPHEMERAL, E_I)
    init.set_keypair_from_public_bytes(Keypair.REMOTE_STATIC, public(S_R))
    init.start_handshake()

    resp = NoiseConnection.from_name(NAME)
    resp.set_as_responder()
    resp.set_psks(PSK)
    resp.set_prologue(PROLOGUE)
    resp.set_keypair_from_private_bytes(Keypair.STATIC, S_R)
    resp.set_keypair_from_private_bytes(Keypair.EPHEMERAL, E_R)
    resp.start_handshake()

    msg1 = bytes(init.write_message(PAYLOAD1))
    assert bytes(resp.read_message(msg1)) == PAYLOAD1
    msg2 = bytes(resp.write_message(PAYLOAD2))
    assert bytes(init.read_message(msg2)) == PAYLOAD2
    assert init.handshake_finished and resp.handshake_finished

    transport = []
    for sender, receiver, side, text in (
        (init, resp, "initiator", b"first transport message"),
        (resp, init, "responder", b"reply"),
        (init, resp, "initiator", b""),
        (init, resp, "initiator", b"x" * 1000),
    ):
        cipher = bytes(sender.encrypt(text))
        assert bytes(receiver.decrypt(cipher)) == text
        transport.append({"from": side, "plaintext": text.hex(), "ciphertext": cipher.hex()})

    out = {
        "protocol": NAME.decode(),
        "generator": "noiseprotocol (independent implementation)",
        "prologue": PROLOGUE.hex(),
        "psk": PSK.hex(),
        "initiator_static_private": S_I.hex(),
        "initiator_ephemeral_private": E_I.hex(),
        "responder_static_private": S_R.hex(),
        "responder_ephemeral_private": E_R.hex(),
        "initiator_static_public": public(S_I).hex(),
        "responder_static_public": public(S_R).hex(),
        "payload1": PAYLOAD1.hex(),
        "payload2": PAYLOAD2.hex(),
        "message1": msg1.hex(),
        "message2": msg2.hex(),
        "handshake_hash": bytes(init.get_handshake_hash()).hex(),
        "transport": transport,
    }
    path = Path(__file__).with_name("vectors.json")
    path.write_text(json.dumps(out, indent=2) + "\n")
    print("wrote", path, file=sys.stderr)


if __name__ == "__main__":
    main()
