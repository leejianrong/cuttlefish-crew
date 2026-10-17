// What the dashboard talks through (ADR-0037). Today it is the same-origin one: `fetch` and `WebSocket` against the daemon's
// address. The relay one (connection.ts) carries the same calls as encrypted streams. Everything above this seam, the
// four requests in api.ts and the terminal's one socket, is written against these two shapes and does not know which it has.

import type { Bytes } from "./x25519";

/** The part of a browser WebSocket the terminal uses. */
export interface SocketLike {
  readonly readyState: number;
  binaryType: BinaryType;
  onopen: ((this: WebSocket, event: Event) => unknown) | null;
  onmessage: ((this: WebSocket, event: MessageEvent) => unknown) | null;
  onclose: ((this: WebSocket, event: CloseEvent) => unknown) | null;
  send(data: string | ArrayBuffer | Bytes): void;
  close(code?: number, reason?: string): void;
}

export interface ApiTransport {
  /** `path` starts with `/api/`; the transport knows where the daemon is. */
  fetch(path: string, init?: RequestInit): Promise<Response>;
  openSocket(path: string): SocketLike;
}

/** The daemon at `baseUrl`, reached directly (loopback, or a private network such as Tailscale). */
export function sameOriginTransport(baseUrl: string): ApiTransport {
  const base = baseUrl.replace(/\/+$/, "");
  return {
    fetch: (path, init) => fetch(`${base}${path}`, init),
    openSocket: (path) => new WebSocket(`${base.replace(/^http/, "ws")}${path}`),
  };
}
