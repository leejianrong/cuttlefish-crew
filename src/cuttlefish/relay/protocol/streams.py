"""Many requests and sockets over one encrypted connection (ADR-0037).

A `StreamMux` is the bookkeeping for one end: it hands out stream ids, cuts bodies into chunks, and
checks every frame that arrives against what is allowed, so a confused or hostile peer is an error
and not a hang. It does no I/O and no flow control (the relay slice adds that when it has a real
socket to measure).

The browser opens streams with odd ids and the daemon with even ones, so the two never collide. A
stream is closed to sending by `END` (or `WS_CLOSE`), and gone once both halves are closed or either
side sends `RESET`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from cuttlefish.relay.protocol.frames import (
    CHUNK_SIZE,
    CONNECTION_STREAM,
    MAX_STREAM_ID,
    Frame,
    FrameType,
    chunks,
)

# : Streams open at once. A peer that opens more is refused instead of being allowed to use memory
# without limit.
MAX_OPEN_STREAMS = 256


class StreamError(Exception):
    """A frame the peer should not have sent, or a call that does not fit the stream."""


class Role(Enum):
    INITIATOR = "initiator"  # the browser
    RESPONDER = "responder"  # the daemon


_OPENING = {FrameType.REQUEST, FrameType.WS_OPEN}
_CLOSING = {FrameType.END, FrameType.WS_CLOSE}
_BODY = {FrameType.DATA, FrameType.WS_DATA}


@dataclass(slots=True)
class _Stream:
    opened_by_us: bool
    websocket: bool
    local_closed: bool = False
    remote_closed: bool = False
    responded: bool = False


class StreamMux:
    def __init__(self, role: Role, *, max_open: int = MAX_OPEN_STREAMS) -> None:
        self._role = role
        self._max_open = max_open
        self._streams: dict[int, _Stream] = {}
        self._next_id = 1 if role is Role.INITIATOR else 2
        self._last_remote_id = 0

    @property
    def open_streams(self) -> int:
        return len(self._streams)

    # -- what we send ------------------------------------------------------------------------------

    def open(self, kind: FrameType, head: bytes) -> tuple[int, Frame]:
        """Start a request or a websocket. Returns the new stream id and the frame that opens it."""
        if kind not in _OPENING:
            raise StreamError("only REQUEST and WS_OPEN open a stream")
        if len(self._streams) >= self._max_open:
            raise StreamError("too many streams are open")
        if self._next_id > MAX_STREAM_ID:
            raise StreamError("this connection has used every stream id")
        stream_id = self._next_id
        self._next_id += 2
        self._streams[stream_id] = _Stream(opened_by_us=True, websocket=kind is FrameType.WS_OPEN)
        return stream_id, Frame(kind, stream_id, head)

    def respond(self, stream_id: int, head: bytes) -> Frame:
        stream = self._get(stream_id)
        if stream.opened_by_us or stream.websocket or stream.responded or stream.local_closed:
            raise StreamError("this stream cannot be answered")
        stream.responded = True
        return Frame(FrameType.RESPONSE, stream_id, head)

    def send(self, stream_id: int, body: bytes, *, chunk_size: int = CHUNK_SIZE) -> list[Frame]:
        """The frames for `body`, in order, cut to `chunk_size`."""
        stream = self._get(stream_id)
        if stream.local_closed:
            raise StreamError("this stream is closed for sending")
        kind = FrameType.WS_DATA if stream.websocket else FrameType.DATA
        if stream.websocket:
            # A websocket message is one frame: cutting it would lose where it ended.
            return [Frame(kind, stream_id, body)]
        return [Frame(kind, stream_id, piece) for piece in chunks(body, chunk_size)]

    def close(self, stream_id: int, code_and_reason: bytes = b"") -> Frame:
        """Close our half: `END` for a request, `WS_CLOSE` for a websocket."""
        stream = self._get(stream_id)
        if stream.local_closed:
            raise StreamError("this stream is already closed for sending")
        stream.local_closed = True
        frame = Frame(
            FrameType.WS_CLOSE if stream.websocket else FrameType.END,
            stream_id,
            code_and_reason if stream.websocket else b"",
        )
        self._drop_if_finished(stream_id)
        return frame

    def reset(self, stream_id: int, reason: bytes = b"") -> Frame:
        self._get(stream_id)
        del self._streams[stream_id]
        return Frame(FrameType.RESET, stream_id, reason)

    @staticmethod
    def pong_for(ping: Frame) -> Frame:
        if ping.type is not FrameType.PING:
            raise StreamError("only a PING is answered with a PONG")
        return Frame(FrameType.PONG, CONNECTION_STREAM, ping.payload)

    # -- what we receive ---------------------------------------------------------------------------

    def receive(self, frame: Frame) -> Frame:
        """Check a frame from the peer against the stream's state and update it. Returns the
        frame, so a caller can
        `mux.receive(decode(data))` and then act on it. Raises `StreamError` for anything not
        allowed.
        """
        kind = frame.type
        if kind in (FrameType.PING, FrameType.PONG):
            return frame
        stream_id = frame.stream
        if kind in _OPENING:
            self._accept_open(frame)
            return frame
        stream = self._streams.get(stream_id)
        if stream is None:
            if kind is FrameType.RESET:
                return frame  # crossed with a normal close; nothing to undo
            raise StreamError(f"frame for a stream that is not open ({stream_id})")
        if kind is FrameType.RESET:
            del self._streams[stream_id]
            return frame
        if stream.remote_closed:
            raise StreamError("the peer sent on a stream it had closed")
        if kind is FrameType.RESPONSE:
            if not stream.opened_by_us or stream.websocket or stream.responded:
                raise StreamError("unexpected RESPONSE")
            stream.responded = True
        elif kind in _BODY:
            if (kind is FrameType.WS_DATA) != stream.websocket:
                raise StreamError("body frame does not match the stream's kind")
        elif kind in _CLOSING:
            if (kind is FrameType.WS_CLOSE) != stream.websocket:
                raise StreamError("close frame does not match the stream's kind")
            stream.remote_closed = True
            self._drop_if_finished(stream_id)
        return frame

    # -- internals ---------------------------------------------------------------------------------

    def _accept_open(self, frame: Frame) -> None:
        stream_id = frame.stream
        theirs_are_odd = self._role is Role.RESPONDER
        if stream_id == CONNECTION_STREAM or (stream_id % 2 == 1) != theirs_are_odd:
            raise StreamError("the peer opened a stream with an id that is ours")
        if stream_id <= self._last_remote_id:
            raise StreamError("the peer reused or went back on a stream id")
        if len(self._streams) >= self._max_open:
            raise StreamError("too many streams are open")
        self._last_remote_id = stream_id
        self._streams[stream_id] = _Stream(
            opened_by_us=False, websocket=frame.type is FrameType.WS_OPEN
        )

    def _get(self, stream_id: int) -> _Stream:
        stream = self._streams.get(stream_id)
        if stream is None:
            raise StreamError(f"no open stream {stream_id}")
        return stream

    def _drop_if_finished(self, stream_id: int) -> None:
        stream = self._streams.get(stream_id)
        if stream is not None and stream.local_closed and stream.remote_closed:
            del self._streams[stream_id]
