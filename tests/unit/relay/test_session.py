"""Unit: a device counts as authenticated only after its first message decrypts (ADR-0037).

CUT-117 found that a caller with the wrong psk still gets the daemon to "finish" its side of the
handshake. These tests hold the daemon's API to that: nothing trusted comes out of `accept` alone.
"""

from __future__ import annotations

import pytest

from cuttlefish.relay.protocol import (
    EstablishedPeer,
    Initiator,
    NoiseError,
    PendingPeer,
    UnknownPeerError,
    accept,
    generate_static,
    public_bytes,
)

PSK = bytes(range(32))


def _call(psk: bytes = PSK) -> tuple[Initiator, bytes, bytes]:
    """A caller that knows the daemon's key, and the daemon's static key and public bytes."""
    daemon_static = generate_static()
    caller = Initiator(generate_static(), public_bytes(daemon_static), psk)
    return caller, public_bytes(daemon_static), daemon_static.private_bytes_raw()


def test_a_caller_with_the_right_secret_is_confirmed_by_its_first_message() -> None:
    daemon = generate_static()
    caller_static = generate_static()
    caller = Initiator(caller_static, public_bytes(daemon), PSK)
    pending = accept(
        daemon, lambda _static: PSK, caller.write_message1(b"hi"), daemon_payload=b"yo"
    )
    assert isinstance(pending, PendingPeer)
    assert pending.claimed_static == public_bytes(caller_static)
    assert pending.payload == b"hi"

    transport, payload = caller.read_message2(pending.reply)
    assert payload == b"yo"
    established = pending.confirm(transport.encrypt(b"first words"))
    assert isinstance(established, EstablishedPeer)
    assert established.peer_static == public_bytes(caller_static)
    assert established.first_message == b"first words"
    assert transport.decrypt(established.transport.encrypt(b"reply")) == b"reply"


def test_a_pending_peer_carries_no_authenticated_key() -> None:
    daemon = generate_static()
    caller = Initiator(generate_static(), public_bytes(daemon), PSK)
    pending = accept(daemon, lambda _s: PSK, caller.write_message1())
    assert not hasattr(pending, "peer_static")
    assert not hasattr(pending, "transport")


def test_a_caller_with_the_wrong_secret_gets_a_reply_but_cannot_be_confirmed() -> None:
    daemon = generate_static()
    caller = Initiator(
        generate_static(), public_bytes(daemon), bytes(32)
    )  # not the daemon's secret
    pending = accept(daemon, lambda _s: PSK, caller.write_message1())
    # The daemon did its side. The caller cannot finish its own, so it sends no first message.
    with pytest.raises(NoiseError):
        caller.read_message2(pending.reply)
    with pytest.raises(NoiseError):
        pending.confirm(b"\x00" * 40)


def test_a_forged_first_message_does_not_confirm() -> None:
    daemon = generate_static()
    caller = Initiator(generate_static(), public_bytes(daemon), PSK)
    pending = accept(daemon, lambda _s: PSK, caller.write_message1())
    with pytest.raises(NoiseError):
        pending.confirm(b"made up bytes that are long enough to be a message")


def test_a_failed_confirmation_ends_the_pending_session() -> None:
    daemon = generate_static()
    caller = Initiator(generate_static(), public_bytes(daemon), PSK)
    pending = accept(daemon, lambda _s: PSK, caller.write_message1())
    transport, _ = caller.read_message2(pending.reply)
    with pytest.raises(NoiseError):
        pending.confirm(b"junk junk junk junk junk junk junk")
    with pytest.raises(NoiseError):  # the real first message is too late: the session is over
        pending.confirm(transport.encrypt(b"hello"))


def test_an_unknown_device_is_refused_and_nothing_is_sent_back() -> None:
    daemon = generate_static()
    caller = Initiator(generate_static(), public_bytes(daemon), PSK)
    with pytest.raises(UnknownPeerError):
        accept(daemon, lambda _static: None, caller.write_message1())


def test_the_secret_is_chosen_per_device() -> None:
    daemon = generate_static()
    alice, bob = generate_static(), generate_static()
    secrets = {public_bytes(alice): bytes([1]) * 32, public_bytes(bob): bytes([2]) * 32}
    for device in (alice, bob):
        caller = Initiator(device, public_bytes(daemon), secrets[public_bytes(device)])
        pending = accept(daemon, lambda s: secrets.get(s), caller.write_message1())
        transport, _ = caller.read_message2(pending.reply)
        assert pending.confirm(transport.encrypt(b"x")).peer_static == public_bytes(device)

    # Alice using Bob's secret is the same as a wrong secret.
    caller = Initiator(alice, public_bytes(daemon), secrets[public_bytes(bob)])
    pending = accept(daemon, lambda s: secrets.get(s), caller.write_message1())
    with pytest.raises(NoiseError):
        caller.read_message2(pending.reply)


def test_a_message_for_another_daemon_is_refused() -> None:
    caller, _, _ = _call()
    with pytest.raises(NoiseError):
        accept(generate_static(), lambda _s: PSK, caller.write_message1())
