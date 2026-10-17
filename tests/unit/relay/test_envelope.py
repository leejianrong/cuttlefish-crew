"""Unit: the client-id envelope between the relay and the daemon (ADR-0037)."""

from __future__ import annotations

import pytest

from cuttlefish.relay.protocol import EnvelopeError, control, parse_control, unwrap, wrap


def test_a_message_round_trips_with_its_client_id() -> None:
    assert unwrap(wrap(7, b"\x00\x01payload")) == (7, b"\x00\x01payload")
    assert unwrap(wrap(2**32 - 1, b"")) == (2**32 - 1, b"")


@pytest.mark.parametrize("client", [0, -1, 2**32])
def test_a_client_id_out_of_range_is_refused(client: int) -> None:
    with pytest.raises(EnvelopeError):
        wrap(client, b"x")


def test_a_short_message_or_client_zero_is_refused() -> None:
    for data in (b"", b"\x00\x00\x00", b"\x00\x00\x00\x00rest"):
        with pytest.raises(EnvelopeError):
            unwrap(data)


def test_control_messages_round_trip() -> None:
    assert parse_control(control("client-open", 3)) == ("client-open", 3, "")
    assert parse_control(control("close-client", 3, "bye")) == ("close-client", 3, "bye")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[]",
        "{}",
        '{"type":"nope","client":1}',
        '{"type":"client-open"}',
        '{"type":"client-open","client":true}',
    ],
)
def test_a_malformed_control_message_is_an_error(text: str) -> None:
    with pytest.raises(EnvelopeError):
        parse_control(text)


def test_an_unknown_control_type_cannot_be_built() -> None:
    with pytest.raises(EnvelopeError):
        control("nope", 1)
