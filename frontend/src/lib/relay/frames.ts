// The plaintext inside one encrypted message: a type byte, a four-byte stream id, a payload (ADR-0037). Mirrors
// cuttlefish.relay.protocol.frames; both are tested against the same byte layout.

import { MAX_MESSAGE_LEN, TAG_LEN } from "./noise";
import type { Bytes } from "./x25519";

export const HEADER_LEN = 5;
export const MAX_PAYLOAD = MAX_MESSAGE_LEN - TAG_LEN - HEADER_LEN;
/** Well under MAX_PAYLOAD, so a frame never grows to the limit by accident. */
export const CHUNK_SIZE = 60000;
/** Stream 0 belongs to the connection (ping and pong); every request or socket gets its own. */
export const CONNECTION_STREAM = 0;
export const MAX_STREAM_ID = 2 ** 32 - 1;

/** The first byte of a WS_DATA payload says what the rest is (see frames.py). A websocket message larger than
 * CHUNK_SIZE goes as several frames. WS_ACCEPTED is empty and comes from the daemon once its app accepted the socket. */
export const WS_TEXT = 0;
export const WS_BINARY = 1;
export const WS_ACCEPTED = 2;

export const FrameType = {
  REQUEST: 1,
  RESPONSE: 2,
  DATA: 3,
  END: 4,
  WS_OPEN: 5,
  WS_DATA: 6,
  WS_CLOSE: 7,
  RESET: 8,
  PING: 9,
  PONG: 10,
} as const;
export type FrameType = (typeof FrameType)[keyof typeof FrameType];

const KNOWN = new Set<number>(Object.values(FrameType));

export class FrameError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "FrameError";
  }
}

export interface Frame {
  readonly type: FrameType;
  readonly stream: number;
  readonly payload: Bytes;
}

/** Builds a frame, refusing one that could not be sent. */
export function frame(type: FrameType, stream: number, payload: Bytes = new Uint8Array(0)): Frame {
  if (!Number.isInteger(stream) || stream < 0 || stream > MAX_STREAM_ID) throw new FrameError("stream id out of range");
  if (payload.length > MAX_PAYLOAD) throw new FrameError("payload too large for one frame");
  const connection = type === FrameType.PING || type === FrameType.PONG;
  if (connection !== (stream === CONNECTION_STREAM)) {
    throw new FrameError("ping and pong belong on stream 0 and nothing else does");
  }
  return { type, stream, payload };
}

export function encode(f: Frame): Bytes {
  const out = new Uint8Array(HEADER_LEN + f.payload.length);
  out[0] = f.type;
  new DataView(out.buffer).setUint32(1, f.stream);
  out.set(f.payload, HEADER_LEN);
  return out;
}

export function decode(data: Bytes): Frame {
  if (data.length < HEADER_LEN) throw new FrameError("frame shorter than its header");
  if (!KNOWN.has(data[0])) throw new FrameError(`unknown frame type ${data[0]}`);
  const stream = new DataView(data.buffer, data.byteOffset, data.byteLength).getUint32(1);
  return frame(data[0] as FrameType, stream, data.slice(HEADER_LEN));
}

/** `body` cut into pieces of at most `size` bytes. An empty body is no pieces. */
export function chunks(body: Bytes, size: number = CHUNK_SIZE): Bytes[] {
  if (!(size > 0 && size <= MAX_PAYLOAD)) throw new FrameError("chunk size out of range");
  const out: Bytes[] = [];
  for (let at = 0; at < body.length; at += size) out.push(body.slice(at, at + size));
  return out;
}
