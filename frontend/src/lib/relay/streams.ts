// Many requests and sockets over one encrypted connection (ADR-0037): ids, chunking and a check of every frame that
// arrives. Mirrors cuttlefish.relay.protocol.streams. No I/O and no flow control.

import type { Bytes } from "./x25519";
import { CHUNK_SIZE, CONNECTION_STREAM, MAX_STREAM_ID, chunks, frame, type Frame, FrameType } from "./frames";

export const MAX_OPEN_STREAMS = 256;

export class StreamError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "StreamError";
  }
}

export type Role = "initiator" | "responder";

interface Stream {
  openedByUs: boolean;
  websocket: boolean;
  localClosed: boolean;
  remoteClosed: boolean;
  responded: boolean;
}

const OPENING = new Set<FrameType>([FrameType.REQUEST, FrameType.WS_OPEN]);

export class StreamMux {
  private readonly streams = new Map<number, Stream>();
  private nextId: number;
  private lastRemoteId = 0;

  constructor(
    private readonly role: Role,
    private readonly maxOpen: number = MAX_OPEN_STREAMS,
  ) {
    this.nextId = role === "initiator" ? 1 : 2;
  }

  get openStreams(): number {
    return this.streams.size;
  }

  // -- what we send --

  open(kind: FrameType, head: Bytes): { stream: number; frame: Frame } {
    if (!OPENING.has(kind)) throw new StreamError("only REQUEST and WS_OPEN open a stream");
    if (this.streams.size >= this.maxOpen) throw new StreamError("too many streams are open");
    if (this.nextId > MAX_STREAM_ID) throw new StreamError("this connection has used every stream id");
    const stream = this.nextId;
    this.nextId += 2;
    this.streams.set(stream, {
      openedByUs: true,
      websocket: kind === FrameType.WS_OPEN,
      localClosed: false,
      remoteClosed: false,
      responded: false,
    });
    return { stream, frame: frame(kind, stream, head) };
  }

  respond(stream: number, head: Bytes): Frame {
    const s = this.get(stream);
    if (s.openedByUs || s.websocket || s.responded || s.localClosed) throw new StreamError("this stream cannot be answered");
    s.responded = true;
    return frame(FrameType.RESPONSE, stream, head);
  }

  send(stream: number, body: Bytes, chunkSize: number = CHUNK_SIZE): Frame[] {
    const s = this.get(stream);
    if (s.localClosed) throw new StreamError("this stream is closed for sending");
    // A websocket message is one frame: cutting it would lose where it ended.
    if (s.websocket) return [frame(FrameType.WS_DATA, stream, body)];
    return chunks(body, chunkSize).map((piece) => frame(FrameType.DATA, stream, piece));
  }

  close(stream: number, codeAndReason: Bytes = new Uint8Array(0)): Frame {
    const s = this.get(stream);
    if (s.localClosed) throw new StreamError("this stream is already closed for sending");
    s.localClosed = true;
    const out = s.websocket
      ? frame(FrameType.WS_CLOSE, stream, codeAndReason)
      : frame(FrameType.END, stream);
    this.dropIfFinished(stream);
    return out;
  }

  reset(stream: number, reason: Bytes = new Uint8Array(0)): Frame {
    this.get(stream);
    this.streams.delete(stream);
    return frame(FrameType.RESET, stream, reason);
  }

  static pongFor(ping: Frame): Frame {
    if (ping.type !== FrameType.PING) throw new StreamError("only a PING is answered with a PONG");
    return frame(FrameType.PONG, CONNECTION_STREAM, ping.payload);
  }

  // -- what we receive --

  receive(f: Frame): Frame {
    const kind = f.type;
    if (kind === FrameType.PING || kind === FrameType.PONG || kind === FrameType.CONTROL) return f;
    if (OPENING.has(kind)) {
      this.acceptOpen(f);
      return f;
    }
    const s = this.streams.get(f.stream);
    if (!s) {
      if (kind === FrameType.RESET) return f; // crossed with a normal close; nothing to undo
      throw new StreamError(`frame for a stream that is not open (${f.stream})`);
    }
    if (kind === FrameType.RESET) {
      this.streams.delete(f.stream);
      return f;
    }
    if (s.remoteClosed) throw new StreamError("the peer sent on a stream it had closed");
    if (kind === FrameType.RESPONSE) {
      if (!s.openedByUs || s.websocket || s.responded) throw new StreamError("unexpected RESPONSE");
      s.responded = true;
    } else if (kind === FrameType.DATA || kind === FrameType.WS_DATA) {
      if ((kind === FrameType.WS_DATA) !== s.websocket) throw new StreamError("body frame does not match the stream's kind");
    } else if (kind === FrameType.END || kind === FrameType.WS_CLOSE) {
      if ((kind === FrameType.WS_CLOSE) !== s.websocket) throw new StreamError("close frame does not match the stream's kind");
      s.remoteClosed = true;
      this.dropIfFinished(f.stream);
    }
    return f;
  }

  // -- internals --

  private acceptOpen(f: Frame): void {
    const theirsAreOdd = this.role === "responder";
    if (f.stream === CONNECTION_STREAM || f.stream % 2 === 1 !== theirsAreOdd) {
      throw new StreamError("the peer opened a stream with an id that is ours");
    }
    if (f.stream <= this.lastRemoteId) throw new StreamError("the peer reused or went back on a stream id");
    if (this.streams.size >= this.maxOpen) throw new StreamError("too many streams are open");
    this.lastRemoteId = f.stream;
    this.streams.set(f.stream, {
      openedByUs: false,
      websocket: f.type === FrameType.WS_OPEN,
      localClosed: false,
      remoteClosed: false,
      responded: false,
    });
  }

  private get(stream: number): Stream {
    const s = this.streams.get(stream);
    if (!s) throw new StreamError(`no open stream ${stream}`);
    return s;
  }

  private dropIfFinished(stream: number): void {
    const s = this.streams.get(stream);
    if (s && s.localClosed && s.remoteClosed) this.streams.delete(stream);
  }
}
