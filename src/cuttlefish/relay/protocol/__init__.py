"""The relay's wire protocol: the Noise handshake, frames and stream bookkeeping (ADR-0037).

Nothing here may import the rest of cuttlefish (fleet, secrets, episodic, agents).
`tests/unit/relay/test_boundary.py` enforces it, so this package can be moved to another repository
later without untangling anything.
"""

from cuttlefish.relay.protocol.envelope import (
    CLIENT_ID_LEN,
    EnvelopeError,
    control,
    parse_control,
    unwrap,
    wrap,
)
from cuttlefish.relay.protocol.frames import (
    CHUNK_SIZE,
    CONNECTION_STREAM,
    HEADER_LEN,
    MAX_PAYLOAD,
    WS_ACCEPTED,
    WS_BINARY,
    WS_TEXT,
    Frame,
    FrameError,
    FrameType,
    chunks,
    decode,
    encode,
)
from cuttlefish.relay.protocol.noise import (
    MAX_MESSAGE_LEN,
    PROLOGUE,
    PROTOCOL_NAME,
    Initiator,
    NoiseError,
    Responder,
    Transport,
    generate_static,
    public_bytes,
    static_from_bytes,
)
from cuttlefish.relay.protocol.session import EstablishedPeer, PendingPeer, UnknownPeerError, accept
from cuttlefish.relay.protocol.streams import MAX_OPEN_STREAMS, Role, StreamError, StreamMux

__all__ = [
    "CHUNK_SIZE",
    "CLIENT_ID_LEN",
    "CONNECTION_STREAM",
    "HEADER_LEN",
    "MAX_MESSAGE_LEN",
    "MAX_OPEN_STREAMS",
    "MAX_PAYLOAD",
    "PROLOGUE",
    "PROTOCOL_NAME",
    "WS_ACCEPTED",
    "WS_BINARY",
    "WS_TEXT",
    "EnvelopeError",
    "EstablishedPeer",
    "Frame",
    "FrameError",
    "FrameType",
    "Initiator",
    "NoiseError",
    "PendingPeer",
    "Responder",
    "Role",
    "StreamError",
    "StreamMux",
    "Transport",
    "UnknownPeerError",
    "accept",
    "chunks",
    "control",
    "decode",
    "encode",
    "generate_static",
    "parse_control",
    "public_bytes",
    "static_from_bytes",
    "unwrap",
    "wrap",
]
