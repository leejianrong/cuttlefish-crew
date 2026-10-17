"""Unit: the frame format inside each encrypted message (ADR-0037)."""

from __future__ import annotations

import pytest

from cuttlefish.relay.protocol import (
    CHUNK_SIZE,
    HEADER_LEN,
    MAX_MESSAGE_LEN,
    MAX_PAYLOAD,
    Frame,
    FrameError,
    FrameType,
    chunks,
    decode,
    encode,
)
from cuttlefish.relay.protocol.noise import TAG_LEN


def test_a_frame_round_trips() -> None:
    frame = Frame(FrameType.DATA, 7, b"hello")
    assert decode(encode(frame)) == frame


def test_the_header_is_five_bytes_big_endian() -> None:
    assert encode(Frame(FrameType.END, 0x01020304)) == bytes([4, 1, 2, 3, 4])
    assert HEADER_LEN == 5


def test_every_type_survives_a_round_trip() -> None:
    for kind in FrameType:
        stream = 0 if kind in (FrameType.PING, FrameType.PONG) else 3
        frame = Frame(kind, stream, b"\x00\xff")
        assert decode(encode(frame)) == frame


def test_a_full_frame_fits_exactly_in_one_noise_message() -> None:
    frame = Frame(FrameType.DATA, 1, b"x" * MAX_PAYLOAD)
    assert len(encode(frame)) + TAG_LEN == MAX_MESSAGE_LEN
    assert CHUNK_SIZE < MAX_PAYLOAD


def test_one_byte_over_is_refused() -> None:
    with pytest.raises(FrameError):
        Frame(FrameType.DATA, 1, b"x" * (MAX_PAYLOAD + 1))


def test_bytes_that_are_not_a_frame_are_refused() -> None:
    with pytest.raises(FrameError):
        decode(b"\x03\x00\x00")  # shorter than the header
    with pytest.raises(FrameError):
        decode(b"\x63\x00\x00\x00\x01")  # no such type
    with pytest.raises(FrameError):
        decode(b"")


def test_ping_and_pong_belong_on_stream_zero_and_nothing_else_does() -> None:
    with pytest.raises(FrameError):
        Frame(FrameType.PING, 5)
    with pytest.raises(FrameError):
        Frame(FrameType.DATA, 0)
    Frame(FrameType.PONG, 0)


def test_a_stream_id_is_a_32_bit_number() -> None:
    Frame(FrameType.DATA, 2**32 - 1)
    with pytest.raises(FrameError):
        Frame(FrameType.DATA, 2**32)
    with pytest.raises(FrameError):
        Frame(FrameType.DATA, -1)


def test_chunks_cut_a_body_without_losing_a_byte() -> None:
    body = bytes(range(256)) * 1000
    pieces = chunks(body, 60000)
    assert [len(p) for p in pieces] == [60000] * 4 + [len(body) - 240000]
    assert b"".join(pieces) == body


def test_an_empty_body_is_no_chunks_and_a_bad_size_is_refused() -> None:
    assert chunks(b"") == []
    with pytest.raises(FrameError):
        chunks(b"abc", 0)
    with pytest.raises(FrameError):
        chunks(b"abc", MAX_PAYLOAD + 1)


def test_the_websocket_kind_bytes_are_the_ones_the_browser_uses() -> None:
    # frontend/src/lib/relay/frames.ts has the same three numbers, and its test pins them too.
    from cuttlefish.relay.protocol import WS_ACCEPTED, WS_BINARY, WS_TEXT

    assert (WS_TEXT, WS_BINARY, WS_ACCEPTED) == (0, 1, 2)
