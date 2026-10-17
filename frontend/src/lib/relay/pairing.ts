// The pairing link's payload (ADR-0037, ADR-0039): where the relay is, which room, the daemon's public key and a one-time secret.
// It rides in a URL fragment, which a browser never sends to a server. The secret works once and for five minutes, and a device
// that uses it still has to be confirmed on the PC.

import { KEY_LEN, type Bytes } from "./x25519";

/** What a pairing link carries: where to connect and the one-time secret. Spent by the first device to use it. */
export interface PairingLink {
  /** `ws://host:port` of the relay. */
  relay: string;
  room: string;
  daemon: Bytes;
  secret: Bytes;
}

export class PairingError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PairingError";
  }
}

export function fromBase64Url(text: string): Bytes {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(text.length / 4) * 4, "=");
  const binary = atob(padded);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

export function toBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** The payload after `#pair=`. Throws `PairingError` with a sentence for a person if it is not usable. */
export function decodePairing(payload: string): PairingLink {
  let body: unknown;
  try {
    body = JSON.parse(new TextDecoder().decode(fromBase64Url(payload)));
  } catch {
    throw new PairingError("This pairing link is damaged. Make a new one on the PC.");
  }
  const b = body as Record<string, unknown> | null;
  if (!b || b.v !== 2 || typeof b.relay !== "string" || typeof b.room !== "string") {
    throw new PairingError("This pairing link is not one this dashboard understands. Make a new one on the PC.");
  }
  if (!/^wss?:\/\//.test(b.relay)) throw new PairingError("The relay address in this link is not a websocket address.");
  if (typeof b.daemon !== "string" || typeof b.pairing !== "string") {
    throw new PairingError("This pairing link is missing a key.");
  }
  const daemon = fromBase64Url(b.daemon);
  const secret = fromBase64Url(b.pairing);
  if (daemon.length !== KEY_LEN || secret.length !== KEY_LEN) throw new PairingError("This pairing link has a key of the wrong size.");
  return { relay: b.relay.replace(/\/+$/, ""), room: b.room, daemon, secret };
}
