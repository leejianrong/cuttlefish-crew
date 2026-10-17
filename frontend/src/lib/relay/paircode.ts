// The six digits both screens show while a device is paired (ADR-0039). They come from the handshake hash, so they match only on
// the two ends of one handshake. Same function as `short_code` in the daemon's `protocol/pairing.py`; both tests pin two vectors.

const DOMAIN = new TextEncoder().encode("cuttlefish-pair-code/1");
export const CODE_DIGITS = 6;

export async function shortCode(handshakeHash: Uint8Array): Promise<string> {
  const input = new Uint8Array(DOMAIN.length + handshakeHash.length);
  input.set(DOMAIN, 0);
  input.set(handshakeHash, DOMAIN.length);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", input));
  const number = ((digest[0] << 24) | (digest[1] << 16) | (digest[2] << 8) | digest[3]) >>> 0;
  return String(number % 10 ** CODE_DIGITS).padStart(CODE_DIGITS, "0");
}

/** `123456` as `123 456`, for people. */
export function formatCode(code: string): string {
  const half = Math.floor(code.length / 2);
  return `${code.slice(0, half)} ${code.slice(half)}`;
}
