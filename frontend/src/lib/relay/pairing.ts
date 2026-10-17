// The pairing link's payload (ADR-0037, CUT-119): where the relay is, which room, the daemon's public key and the secret for the
// handshake. It rides in a URL fragment, which a browser never sends to a server. This is the config-only pairing of this slice;
// CUT-120 replaces it with one-time pairing and a per-device secret.

import { KEY_LEN, type Bytes } from "./x25519";

export interface RelayConfig {
  /** `ws://host:port` of the relay. */
  relay: string;
  room: string;
  daemon: Bytes;
  psk: Bytes;
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

/** The payload after `#relay=`. Throws `PairingError` with a sentence for a person if it is not usable. */
export function decodePairing(payload: string): RelayConfig {
  let body: unknown;
  try {
    body = JSON.parse(new TextDecoder().decode(fromBase64Url(payload)));
  } catch {
    throw new PairingError("This pairing link is damaged. Copy it again from the daemon's output.");
  }
  const b = body as Record<string, unknown> | null;
  if (!b || b.v !== 1 || typeof b.relay !== "string" || typeof b.room !== "string") {
    throw new PairingError("This pairing link is not one this dashboard understands.");
  }
  if (!/^wss?:\/\//.test(b.relay)) throw new PairingError("The relay address in this link is not a websocket address.");
  if (typeof b.daemon !== "string" || typeof b.psk !== "string") {
    throw new PairingError("This pairing link is missing a key.");
  }
  const daemon = fromBase64Url(b.daemon);
  const psk = fromBase64Url(b.psk);
  if (daemon.length !== KEY_LEN || psk.length !== KEY_LEN) throw new PairingError("This pairing link has a key of the wrong size.");
  return { relay: b.relay.replace(/\/+$/, ""), room: b.room, daemon, psk };
}
