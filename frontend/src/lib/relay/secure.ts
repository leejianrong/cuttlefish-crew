// WebCrypto exists only in a secure context: an https page, or http on localhost. The relay's handshake needs it (X25519, AES-GCM,
// HMAC and SHA-256 all come from `crypto.subtle`), so a page opened at plain http on a Tailscale or LAN address cannot pair or use the
// relay at all. Checked in Chromium and Firefox on the Tailscale address: `isSecureContext` is false and `crypto.subtle` is undefined.

/** Whether this page can do the relay's cryptography. */
export function webCryptoAvailable(
  env: { isSecureContext?: boolean; crypto?: { subtle?: unknown } } = globalThis,
): boolean {
  return env.isSecureContext !== false && typeof env.crypto?.subtle === "object" && env.crypto.subtle !== null;
}

/** Whether a browser treats this address as a secure context: https, or http on this machine itself. */
export function isSecureAddress(address: string): boolean {
  let url: URL;
  try {
    url = new URL(address);
  } catch {
    return false;
  }
  if (url.protocol === "https:") return true;
  return url.protocol === "http:" && isLoopbackHost(url.hostname);
}

export function isLoopbackHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "[::1]" || hostname === "::1" || /^127\.\d+\.\d+\.\d+$/.test(hostname);
}

/** What a person should be told on a page that cannot pair. */
export const INSECURE_PAGE_MESSAGE =
  "This page was opened at a plain http address, which browsers do not treat as secure, so it cannot do the encryption that pairing needs. Pairing works from an https address, or on the PC itself at http://localhost. To use this device now, sign in directly with the password.";

/** What the PC's Devices screen says about the address a phone is going to open, or null when it can work. */
export function pairingAddressProblem(address: string): string | null {
  if (isSecureAddress(address) && !isLoopbackHost(safeHost(address))) return null;
  if (isLoopbackHost(safeHost(address))) {
    return "A phone cannot open this address, because it is the PC itself, and a phone's browser only allows pairing from an https address.";
  }
  return "A phone's browser only allows pairing from an https address, and this one is plain http, so pairing from it will fail. A plain-http Tailscale address works for signing in with the password, not for pairing.";
}

function safeHost(address: string): string {
  try {
    return new URL(address).hostname;
  } catch {
    return "";
  }
}
