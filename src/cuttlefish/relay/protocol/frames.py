"""The plaintext inside one encrypted message: one type byte, a four-byte stream id, then a payload
(ADR-0037).

One websocket binary message carries one Noise transport message, which carries one frame. A Noise
message is at most 65535 bytes with its 16-byte tag, so a frame's payload is at most `MAX_PAYLOAD`,
and larger bodies are cut into chunks of `CHUNK_SIZE`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

from cuttlefish.relay.protocol.noise import MAX_MESSAGE_LEN, TAG_LEN

HEADER_LEN = 5
MAX_PAYLOAD = MAX_MESSAGE_LEN - TAG_LEN - HEADER_LEN
#: Well under `MAX_PAYLOAD`, so a frame never grows to the limit by accident.
CHUNK_SIZE = 60000
#: Stream 0 belongs to the connection (ping and pong); every request or socket gets its own.
CONNECTION_STREAM = 0
MAX_STREAM_ID = 2**32 - 1


class FrameError(ValueError):
    """Bytes that are not a valid frame."""


class FrameType(IntEnum):
    # : Opens a stream for an HTTP request. The payload is the request head (method, path,
    # headers) as JSON.
    REQUEST = 1
    #: The answer to a request: the response head (status, headers) as JSON.
    RESPONSE = 2
    #: A piece of a body, either way.
    DATA = 3
    #: The sender has nothing more to say on this stream.
    END = 4
    #: Opens a stream for a websocket. The payload is the head (path) as JSON.
    WS_OPEN = 5
    #: One websocket message, either way.
    WS_DATA = 6
    # : The sender closes its half of the websocket. The payload is a two-byte close code and
    # an optional reason.
    WS_CLOSE = 7
    #: Abandons a stream both ways. The payload is an optional reason.
    RESET = 8
    PING = 9
    PONG = 10


#: The first byte of a `WS_DATA` payload says what the rest is. A websocket message larger than
#: `CHUNK_SIZE` is sent as several frames (the daemon's terminal is a byte stream, so where a
#: message ended does not matter there). `WS_ACCEPTED` is empty and goes daemon to browser once
#: the app has accepted the socket, so the browser can tell "open" from "refused".
WS_TEXT = 0
WS_BINARY = 1
WS_ACCEPTED = 2

_CONNECTION_FRAMES = {FrameType.PING, FrameType.PONG}


@dataclass(frozen=True, slots=True)
class Frame:
    type: FrameType
    stream: int
    payload: bytes = b""

    def __post_init__(self) -> None:
        if not 0 <= self.stream <= MAX_STREAM_ID:
            raise FrameError("stream id out of range")
        if len(self.payload) > MAX_PAYLOAD:
            raise FrameError("payload too large for one frame")
        if (self.type in _CONNECTION_FRAMES) != (self.stream == CONNECTION_STREAM):
            raise FrameError("ping and pong belong on stream 0 and nothing else does")


def encode(frame: Frame) -> bytes:
    return struct.pack(">BI", frame.type, frame.stream) + frame.payload


def decode(data: bytes) -> Frame:
    if len(data) < HEADER_LEN:
        raise FrameError("frame shorter than its header")
    kind, stream = struct.unpack(">BI", data[:HEADER_LEN])
    try:
        frame_type = FrameType(kind)
    except ValueError as exc:
        raise FrameError(f"unknown frame type {kind}") from exc
    return Frame(frame_type, stream, data[HEADER_LEN:])


def chunks(body: bytes, size: int = CHUNK_SIZE) -> list[bytes]:
    """`body` cut into pieces of at most `size` bytes. An empty body is no pieces."""
    if not 0 < size <= MAX_PAYLOAD:
        raise FrameError("chunk size out of range")
    return [body[i : i + size] for i in range(0, len(body), size)]
