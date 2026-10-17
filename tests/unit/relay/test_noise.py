"""Unit: the Noise handshake and transport (ADR-0037).

The first group replays bytes made by an implementation we did not write, so a mistake in our
reading of the specification cannot hide behind a matching mistake in our own other end.
"""

from __future__ import annotations

from typing import Any

import pytest

from cuttlefish.relay.protocol import noise as nz


def _key(vectors: dict[str, Any], name: str) -> nz.X25519PrivateKey:
    return nz.static_from_bytes(bytes.fromhex(vectors[name]))


def _hex(vectors: dict[str, Any], name: str) -> bytes:
    return bytes.fromhex(vectors[name])


def _initiator(vectors: dict[str, Any]) -> nz.Initiator:
    return nz.Initiator(
        _key(vectors, "initiator_static_private"),
        _hex(vectors, "responder_static_public"),
        _hex(vectors, "psk"),
        prologue=_hex(vectors, "prologue"),
        ephemeral=_key(vectors, "initiator_ephemeral_private"),
    )


def _responder(vectors: dict[str, Any]) -> nz.Responder:
    return nz.Responder(
        _key(vectors, "responder_static_private"),
        prologue=_hex(vectors, "prologue"),
        ephemeral=_key(vectors, "responder_ephemeral_private"),
    )


def test_the_public_keys_match_the_vectors(vectors: dict[str, Any]) -> None:
    assert nz.public_bytes(_key(vectors, "initiator_static_private")) == _hex(
        vectors, "initiator_static_public"
    )
    assert nz.public_bytes(_key(vectors, "responder_static_private")) == _hex(
        vectors, "responder_static_public"
    )


def test_our_initiator_writes_the_same_first_message(vectors: dict[str, Any]) -> None:
    message1 = _initiator(vectors).write_message1(_hex(vectors, "payload1"))
    assert message1 == _hex(vectors, "message1")


def test_our_responder_reads_the_other_implementations_first_message(
    vectors: dict[str, Any],
) -> None:
    responder = _responder(vectors)
    static, payload = responder.read_message1(_hex(vectors, "message1"))
    assert static == _hex(vectors, "initiator_static_public")
    assert payload == _hex(vectors, "payload1")


def test_our_responder_writes_the_same_second_message(vectors: dict[str, Any]) -> None:
    responder = _responder(vectors)
    responder.read_message1(_hex(vectors, "message1"))
    message2, transport = responder.write_message2(_hex(vectors, "psk"), _hex(vectors, "payload2"))
    assert message2 == _hex(vectors, "message2")
    assert transport.handshake_hash == _hex(vectors, "handshake_hash")


def test_our_initiator_finishes_on_the_other_implementations_second_message(
    vectors: dict[str, Any],
) -> None:
    initiator = _initiator(vectors)
    initiator.write_message1(_hex(vectors, "payload1"))
    transport, payload = initiator.read_message2(_hex(vectors, "message2"))
    assert payload == _hex(vectors, "payload2")
    assert transport.handshake_hash == _hex(vectors, "handshake_hash")


def test_transport_messages_match_in_both_directions(vectors: dict[str, Any]) -> None:
    initiator = _initiator(vectors)
    initiator.write_message1(_hex(vectors, "payload1"))
    from_initiator, _ = initiator.read_message2(_hex(vectors, "message2"))
    responder = _responder(vectors)
    responder.read_message1(_hex(vectors, "message1"))
    _, from_responder = responder.write_message2(_hex(vectors, "psk"), _hex(vectors, "payload2"))

    for item in vectors["transport"]:
        plaintext, cipher = bytes.fromhex(item["plaintext"]), bytes.fromhex(item["ciphertext"])
        sender, receiver = (
            (from_initiator, from_responder)
            if item["from"] == "initiator"
            else (from_responder, from_initiator)
        )
        assert sender.encrypt(plaintext) == cipher
        assert receiver.decrypt(cipher) == plaintext


# -- a handshake with fresh keys --


def _pair() -> tuple[nz.X25519PrivateKey, nz.X25519PrivateKey, bytes]:
    return nz.generate_static(), nz.generate_static(), bytes(range(32))


def _handshake(
    initiator_static: nz.X25519PrivateKey,
    responder_static: nz.X25519PrivateKey,
    psk_initiator: bytes,
    psk_responder: bytes,
) -> tuple[nz.Transport, nz.Transport]:
    initiator = nz.Initiator(initiator_static, nz.public_bytes(responder_static), psk_initiator)
    responder = nz.Responder(responder_static)
    responder.read_message1(initiator.write_message1(b"hi"))
    message2, theirs = responder.write_message2(psk_responder)
    ours, _ = initiator.read_message2(message2)
    return ours, theirs


def test_a_fresh_handshake_carries_traffic_both_ways() -> None:
    initiator_static, responder_static, psk = _pair()
    ours, theirs = _handshake(initiator_static, responder_static, psk, psk)
    assert theirs.decrypt(ours.encrypt(b"to the daemon")) == b"to the daemon"
    assert ours.decrypt(theirs.encrypt(b"to the browser")) == b"to the browser"
    assert ours.handshake_hash == theirs.handshake_hash


def test_a_wrong_psk_is_rejected_by_the_initiator() -> None:
    initiator_static, responder_static, psk = _pair()
    with pytest.raises(nz.NoiseError):
        _handshake(initiator_static, responder_static, psk, bytes(32))


def test_a_wrong_responder_key_cannot_read_the_first_message() -> None:
    initiator_static, responder_static, psk = _pair()
    initiator = nz.Initiator(initiator_static, nz.public_bytes(responder_static), psk)
    impostor = nz.Responder(nz.generate_static())
    with pytest.raises(nz.NoiseError):
        impostor.read_message1(initiator.write_message1())


def test_a_different_prologue_fails() -> None:
    initiator_static, responder_static, psk = _pair()
    initiator = nz.Initiator(
        initiator_static, nz.public_bytes(responder_static), psk, prologue=b"another product"
    )
    with pytest.raises(nz.NoiseError):
        nz.Responder(responder_static).read_message1(initiator.write_message1())


@pytest.mark.parametrize("where", [0, 40, 90])
def test_a_tampered_first_message_is_rejected(where: int) -> None:
    initiator_static, responder_static, psk = _pair()
    message = bytearray(
        nz.Initiator(initiator_static, nz.public_bytes(responder_static), psk).write_message1(b"x")
    )
    message[where] ^= 1
    with pytest.raises(nz.NoiseError):
        nz.Responder(responder_static).read_message1(bytes(message))


def test_a_tampered_second_message_is_rejected() -> None:
    initiator_static, responder_static, psk = _pair()
    initiator = nz.Initiator(initiator_static, nz.public_bytes(responder_static), psk)
    responder = nz.Responder(responder_static)
    responder.read_message1(initiator.write_message1())
    message2 = bytearray(responder.write_message2(psk)[0])
    message2[-1] ^= 1
    with pytest.raises(nz.NoiseError):
        initiator.read_message2(bytes(message2))


@pytest.mark.parametrize("size", [0, 10, 47, 70])
def test_a_short_first_message_is_rejected(size: int) -> None:
    with pytest.raises(nz.NoiseError):
        nz.Responder(nz.generate_static()).read_message1(b"\x00" * size)


def test_a_low_order_ephemeral_key_is_rejected() -> None:
    _, responder_static, _ = _pair()
    with pytest.raises(nz.NoiseError):
        nz.Responder(responder_static).read_message1(b"\x00" * 32 + b"\x01" * 64)


def test_message_order_is_enforced() -> None:
    initiator_static, responder_static, psk = _pair()
    initiator = nz.Initiator(initiator_static, nz.public_bytes(responder_static), psk)
    with pytest.raises(nz.NoiseError):
        initiator.read_message2(b"\x00" * 64)
    message1 = initiator.write_message1()
    with pytest.raises(nz.NoiseError):
        initiator.write_message1()
    responder = nz.Responder(responder_static)
    with pytest.raises(nz.NoiseError):
        responder.write_message2(psk)
    responder.read_message1(message1)
    with pytest.raises(nz.NoiseError):
        responder.read_message1(message1)


def test_a_psk_that_is_not_32_bytes_is_refused() -> None:
    initiator_static, responder_static, _ = _pair()
    with pytest.raises(nz.NoiseError):
        nz.Initiator(initiator_static, nz.public_bytes(responder_static), b"short")
    responder = nz.Responder(responder_static)
    responder.read_message1(
        nz.Initiator(
            initiator_static, nz.public_bytes(responder_static), bytes(32)
        ).write_message1()
    )
    with pytest.raises(nz.NoiseError):
        responder.write_message2(b"short")


# -- the transport --


def test_a_tampered_transport_message_ends_the_session() -> None:
    initiator_static, responder_static, psk = _pair()
    ours, theirs = _handshake(initiator_static, responder_static, psk, psk)
    cipher = bytearray(ours.encrypt(b"hello"))
    cipher[0] ^= 1
    with pytest.raises(nz.NoiseError):
        theirs.decrypt(bytes(cipher))
    # Nothing to retry on an ordered transport: a good message after a bad one is refused too.
    with pytest.raises(nz.NoiseError):
        theirs.decrypt(ours.encrypt(b"after"))
    with pytest.raises(nz.NoiseError):
        theirs.encrypt(b"anything")


def test_a_replayed_or_reordered_message_is_rejected() -> None:
    initiator_static, responder_static, psk = _pair()
    ours, theirs = _handshake(initiator_static, responder_static, psk, psk)
    first, second = ours.encrypt(b"one"), ours.encrypt(b"two")
    with pytest.raises(nz.NoiseError):
        theirs.decrypt(second)

    ours, theirs = _handshake(initiator_static, responder_static, psk, psk)
    first = ours.encrypt(b"one")
    assert theirs.decrypt(first) == b"one"
    with pytest.raises(nz.NoiseError):
        theirs.decrypt(first)


def test_the_largest_message_fits_and_one_more_byte_does_not() -> None:
    initiator_static, responder_static, psk = _pair()
    ours, theirs = _handshake(initiator_static, responder_static, psk, psk)
    biggest = b"x" * (nz.MAX_MESSAGE_LEN - nz.TAG_LEN)
    cipher = ours.encrypt(biggest)
    assert len(cipher) == nz.MAX_MESSAGE_LEN
    assert theirs.decrypt(cipher) == biggest
    with pytest.raises(nz.NoiseError):
        ours.encrypt(biggest + b"x")


def test_a_session_ends_before_its_nonce_would_wrap() -> None:
    initiator_static, responder_static, psk = _pair()
    ours, _ = _handshake(initiator_static, responder_static, psk, psk)
    ours._sent = 2**64 - 2  # the last nonce Noise permits before it must rekey
    ours.encrypt(b"last")
    with pytest.raises(nz.NoiseError):
        ours.encrypt(b"one too many")
