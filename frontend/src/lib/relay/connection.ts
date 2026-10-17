// The browser's end of one relay connection (ADR-0037, CUT-119): a websocket to the relay, a Noise handshake with the daemon
// through it, and the dashboard's requests and sockets carried as streams over what that agrees. The relay sees ciphertext.
//
// Transport order matters: a Noise transport counts messages, so everything is encrypted and decrypted one at a time, in the
// order it was sent and arrived, through two promise chains.

import {
  CHUNK_SIZE,
  FrameType,
  WS_ACCEPTED,
  WS_BINARY,
  WS_TEXT,
  decode,
  encode,
  frame,
  type Frame,
} from "./frames";
import { Initiator, type Transport as NoiseTransport } from "./noise";
import type { RelayConfig } from "./pairing";
import { StreamMux } from "./streams";
import type { ApiTransport, SocketLike } from "./transport";
import { getX25519, type Bytes } from "./x25519";

export class RelayError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RelayError";
  }
}

const encoder = new TextEncoder();
const decoder = new TextDecoder();
const HANDSHAKE_TIMEOUT_MS = 10_000;
const PING_EVERY_MS = 20_000;
/** A response with these statuses may not have a body, so `new Response` must be given none. */
const NO_BODY = new Set([101, 204, 205, 304]);

type Pending = {
  status: number;
  headers: [string, string][];
  chunks: Bytes[];
  gotHead: boolean;
  resolve: (r: Response) => void;
  reject: (e: unknown) => void;
};

/** A `CloseEvent`, or where the runtime has none (Node before 23, in tests) an `Event` that carries the same fields. */
function closeEvent(code: number, reason: string): CloseEvent {
  if (typeof CloseEvent === "function") return new CloseEvent("close", { code, reason });
  return Object.assign(new Event("close"), { code, reason, wasClean: code === 1000 }) as CloseEvent;
}

/** The part of a WebSocket the terminal uses, driven by a relay stream instead of the network. */
export class RelaySocket implements SocketLike {
  readyState = 0;
  binaryType: BinaryType = "blob";
  onopen: ((this: WebSocket, event: Event) => unknown) | null = null;
  onmessage: ((this: WebSocket, event: MessageEvent) => unknown) | null = null;
  onclose: ((this: WebSocket, event: CloseEvent) => unknown) | null = null;
  private link: { sendData(kind: number, data: Bytes): void; sendClose(code: number, reason: string): void } | null = null;

  /** Called by the connection once the stream is open. */
  attach(link: NonNullable<RelaySocket["link"]>): void {
    this.link = link;
  }

  send(data: string | ArrayBuffer | Bytes): void {
    if (this.readyState !== 1 || !this.link) throw new RelayError("the socket is not open");
    if (typeof data === "string") this.link.sendData(WS_TEXT, encoder.encode(data));
    else this.link.sendData(WS_BINARY, data instanceof Uint8Array ? data : new Uint8Array(data));
  }

  close(code = 1000, reason = ""): void {
    if (this.readyState >= 2) return;
    this.readyState = 2;
    if (this.link) this.link.sendClose(code, reason);
    else this.finish(code, reason);
  }

  /** The daemon's app accepted the socket. */
  accepted(): void {
    if (this.readyState !== 0) return;
    this.readyState = 1;
    this.onopen?.call(this as unknown as WebSocket, new Event("open"));
  }

  message(kind: number, data: Bytes): void {
    if (this.readyState !== 1) return;
    const ws = this as unknown as WebSocket;
    if (kind === WS_TEXT) this.onmessage?.call(ws, new MessageEvent("message", { data: decoder.decode(data) }));
    else if (kind === WS_BINARY) {
      this.onmessage?.call(ws, new MessageEvent("message", { data: data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) }));
    }
  }

  /** Closed from the other side, or the connection under it died (code 1006). */
  finish(code: number, reason: string): void {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.onclose?.call(this as unknown as WebSocket, closeEvent(code, reason));
  }
}

export class RelayConnection {
  private readonly mux = new StreamMux("initiator");
  private readonly requests = new Map<number, Pending>();
  private readonly sockets = new Map<number, RelaySocket>();
  private inbound: Promise<void> = Promise.resolve();
  private outbound: Promise<void> = Promise.resolve();
  private pinger: ReturnType<typeof setInterval> | null = null;
  closed = false;
  onClose: ((reason: string) => void) | null = null;

  private constructor(
    private readonly ws: WebSocket,
    private readonly noise: NoiseTransport,
  ) {}

  /** Connects, runs the handshake, and proves the keys with a first message the daemon must decrypt. */
  static async connect(config: RelayConfig): Promise<RelayConnection> {
    const ws = new WebSocket(`${config.relay}/ws/client/${config.room}`);
    ws.binaryType = "arraybuffer";
    const messages: Bytes[] = [];
    let wake: (() => void) | null = null;
    let ended: { code: number; reason: string } | null = null;
    ws.onmessage = (event) => {
      messages.push(new Uint8Array(event.data as ArrayBuffer));
      wake?.();
    };
    ws.onclose = (event) => {
      ended = { code: event.code, reason: event.reason };
      wake?.();
    };
    const next = async (): Promise<Bytes> => {
      const deadline = Date.now() + HANDSHAKE_TIMEOUT_MS;
      while (messages.length === 0) {
        if (ended) throw new RelayError(closeText(ended.code, ended.reason));
        if (Date.now() > deadline) throw new RelayError("The daemon did not answer through the relay.");
        await new Promise<void>((resolve) => {
          wake = resolve;
          setTimeout(resolve, 200);
        });
      }
      return messages.shift()!;
    };
    try {
      await new Promise<void>((resolve, reject) => {
        ws.onopen = () => resolve();
        ws.addEventListener("error", () => reject(new RelayError("Could not reach the relay.")), { once: true });
      });
      const curve = await getX25519();
      // A new key for every page load in this slice. A kept, non-extractable key and a daemon that knows it are CUT-120.
      const initiator = await Initiator.create(await curve.generate(), config.daemon, config.psk, { x25519: curve });
      ws.send(await initiator.writeMessage1());
      const { transport } = await initiator.readMessage2(await next());
      const connection = new RelayConnection(ws, transport);
      // The daemon trusts this browser only once this decrypts, so the first message is a ping, and the pong proves the reverse.
      ws.send(await transport.encrypt(encode(frame(FrameType.PING, 0, encoder.encode("hello")))));
      const pong = decode(await transport.decrypt(await next()));
      if (pong.type !== FrameType.PONG) throw new RelayError("The daemon did not accept this pairing link.");
      connection.start(messages, () => ended);
      return connection;
    } catch (error) {
      ws.close();
      throw error instanceof Error ? error : new RelayError(String(error));
    }
  }

  private start(queued: Bytes[], endedNow: () => { code: number; reason: string } | null): void {
    this.ws.onmessage = (event) => this.receive(new Uint8Array(event.data as ArrayBuffer));
    this.ws.onclose = (event) => this.fail(closeText(event.code, event.reason));
    for (const message of queued.splice(0)) this.receive(message);
    const ended = endedNow();
    if (ended) this.fail(closeText(ended.code, ended.reason));
    this.pinger = setInterval(() => {
      void this.send(frame(FrameType.PING, 0, new Uint8Array(0))).catch(() => undefined);
    }, PING_EVERY_MS);
  }

  private receive(data: Bytes): void {
    this.inbound = this.inbound
      .then(async () => {
        if (this.closed) return;
        this.handle(this.mux.receive(decode(await this.noise.decrypt(data))));
      })
      .catch((error: unknown) => this.fail(error instanceof Error ? error.message : String(error)));
  }

  private handle(f: Frame): void {
    const id = f.stream;
    switch (f.type) {
      case FrameType.PING:
        void this.send(StreamMux.pongFor(f)).catch(() => undefined);
        break;
      case FrameType.RESPONSE: {
        const pending = this.requests.get(id);
        if (!pending) break;
        const head = JSON.parse(decoder.decode(f.payload)) as { status: number; headers: [string, string][] };
        pending.status = head.status;
        pending.headers = head.headers;
        pending.gotHead = true;
        break;
      }
      case FrameType.DATA:
        this.requests.get(id)?.chunks.push(f.payload);
        break;
      case FrameType.END: {
        const pending = this.requests.get(id);
        if (!pending) break;
        this.requests.delete(id);
        const headers = new Headers();
        for (const [k, v] of pending.headers) headers.append(k, v);
        const body = NO_BODY.has(pending.status) ? null : new Blob(pending.chunks);
        pending.resolve(new Response(body, { status: pending.status, headers }));
        break;
      }
      case FrameType.WS_DATA: {
        const socket = this.sockets.get(id);
        if (!socket || f.payload.length === 0) break;
        const kind = f.payload[0];
        if (kind === WS_ACCEPTED) socket.accepted();
        else socket.message(kind, f.payload.slice(1));
        break;
      }
      case FrameType.WS_CLOSE: {
        const socket = this.sockets.get(id);
        this.sockets.delete(id);
        const code = f.payload.length >= 2 ? (f.payload[0] << 8) | f.payload[1] : 1005;
        socket?.finish(code, decoder.decode(f.payload.slice(2)));
        break;
      }
      case FrameType.RESET: {
        const pending = this.requests.get(id);
        this.requests.delete(id);
        pending?.reject(new RelayError("The daemon abandoned this request."));
        const socket = this.sockets.get(id);
        this.sockets.delete(id);
        socket?.finish(1011, "reset");
        break;
      }
      default:
        break;
    }
  }

  private send(...frames: Frame[]): Promise<void> {
    const run = async () => {
      if (this.closed) throw new RelayError("The relay connection is closed.");
      for (const f of frames) this.ws.send(await this.noise.encrypt(encode(f)));
    };
    const result = this.outbound.then(run);
    this.outbound = result.catch(() => undefined);
    return result;
  }

  async fetch(path: string, init: RequestInit = {}): Promise<Response> {
    const headers: [string, string][] = [];
    new Headers(init.headers).forEach((value, key) => headers.push([key, value]));
    const body = typeof init.body === "string" ? encoder.encode(init.body) : init.body instanceof Uint8Array ? init.body : new Uint8Array(0);
    const { stream, frame: opening } = this.mux.open(
      FrameType.REQUEST,
      encoder.encode(JSON.stringify({ method: init.method ?? "GET", path, headers })),
    );
    const response = new Promise<Response>((resolve, reject) => {
      this.requests.set(stream, { status: 0, headers: [], chunks: [], gotHead: false, resolve, reject });
    });
    const abort = () => {
      const pending = this.requests.get(stream);
      if (!pending) return;
      this.requests.delete(stream);
      void this.send(this.mux.reset(stream)).catch(() => undefined);
      pending.reject(new DOMException("The request was aborted.", "AbortError"));
    };
    init.signal?.addEventListener("abort", abort, { once: true });
    try {
      await this.send(opening, ...this.mux.send(stream, body as Bytes), this.mux.close(stream));
    } catch (error) {
      this.requests.delete(stream);
      throw error;
    }
    return response;
  }

  openSocket(path: string, socket: RelaySocket): void {
    let stream: number;
    try {
      const opened = this.mux.open(FrameType.WS_OPEN, encoder.encode(JSON.stringify({ path })));
      stream = opened.stream;
      this.sockets.set(stream, socket);
      socket.attach({
        sendData: (kind, data) => {
          // A message larger than a frame goes as several: fine for a terminal, which is a byte stream.
          for (let at = 0; at < Math.max(data.length, 1); at += CHUNK_SIZE - 1) {
            const piece = data.slice(at, at + CHUNK_SIZE - 1);
            const payload = new Uint8Array(piece.length + 1);
            payload[0] = kind;
            payload.set(piece, 1);
            void this.send(...this.mux.send(stream, payload)).catch(() => socket.finish(1006, "connection lost"));
          }
        },
        sendClose: (code, reason) => {
          const tail = encoder.encode(reason).slice(0, 120);
          const payload = new Uint8Array(2 + tail.length);
          payload[0] = code >> 8;
          payload[1] = code & 0xff;
          payload.set(tail, 2);
          try {
            void this.send(this.mux.close(stream, payload)).catch(() => undefined);
          } catch {
            /* already closed */
          }
          this.sockets.delete(stream);
          socket.finish(code, reason);
        },
      });
      void this.send(opened.frame).catch(() => socket.finish(1006, "connection lost"));
    } catch {
      socket.finish(1006, "connection lost");
    }
  }

  private fail(reason: string): void {
    if (this.closed) return;
    this.closed = true;
    if (this.pinger) clearInterval(this.pinger);
    for (const pending of this.requests.values()) pending.reject(new RelayError(reason));
    this.requests.clear();
    for (const socket of this.sockets.values()) socket.finish(1006, reason);
    this.sockets.clear();
    try {
      this.ws.close();
    } catch {
      /* already closed */
    }
    this.onClose?.(reason);
  }

  close(): void {
    this.fail("closed");
  }
}

/** Words for a relay close, for a person. 4503 and 4429 are the relay's own codes. */
export function closeText(code: number, reason: string): string {
  if (code === 4503) return "The daemon is not connected to the relay. Is `cuttlefish serve --relay` running?";
  if (code === 4429) return "The relay is full.";
  if (code === 1008) return reason || "The daemon did not accept this pairing link.";
  return reason ? `The relay connection closed: ${reason}` : "The relay connection closed.";
}

/** A `Transport` over a relay that connects when first used and again after it drops. */
export class RelayTransport implements ApiTransport {
  private connection: Promise<RelayConnection> | null = null;

  constructor(private readonly config: RelayConfig) {}

  private ensure(): Promise<RelayConnection> {
    if (!this.connection) {
      const attempt = RelayConnection.connect(this.config).then((connection) => {
        connection.onClose = () => {
          if (this.connection === attempt) this.connection = null;
        };
        return connection;
      });
      attempt.catch(() => {
        if (this.connection === attempt) this.connection = null;
      });
      this.connection = attempt;
    }
    return this.connection;
  }

  async fetch(path: string, init?: RequestInit): Promise<Response> {
    return (await this.ensure()).fetch(path, init);
  }

  openSocket(path: string): SocketLike {
    const socket = new RelaySocket();
    this.ensure().then(
      (connection) => connection.openSocket(path, socket),
      () => socket.finish(1006, "Could not reach the daemon through the relay."),
    );
    return socket;
  }

  close(): void {
    const current = this.connection;
    this.connection = null;
    void current?.then((connection) => connection.close(), () => undefined);
  }
}
