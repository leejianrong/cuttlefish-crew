// Test helper: the vectors file made by an independent Noise implementation (tests/fixtures/relay/make_vectors.py),
// shared with the Python tests.

import vectorsFile from "../../../../tests/fixtures/relay/vectors.json";
import type { Bytes } from "./x25519";

export interface Vectors {
  prologue: string;
  psk: string;
  initiator_static_private: string;
  initiator_ephemeral_private: string;
  responder_static_public: string;
  initiator_static_public: string;
  payload1: string;
  payload2: string;
  message1: string;
  message2: string;
  handshake_hash: string;
  transport: { from: "initiator" | "responder"; plaintext: string; ciphertext: string }[];
}

export const vectors: Vectors = vectorsFile as Vectors;

export function hex(text: string): Bytes {
  return Uint8Array.from(text.match(/../g) ?? [], (pair) => parseInt(pair, 16));
}

export function toHex(bytes: Uint8Array): string {
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}
