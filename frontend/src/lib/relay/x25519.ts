// X25519 for the relay handshake (ADR-0037). The browser's own WebCrypto is preferred, because it can hold a
// private key that script can use but never read out. Where WebCrypto lacks X25519 (an older Safari: the sources
// disagree on its date) a pure JavaScript implementation takes over. That path keeps the key as bytes the page can
// read, which is weaker, so the caller can ask which one it got.

import { x25519 } from "@noble/curves/ed25519.js";

export const KEY_LEN = 32;

/** What WebCrypto accepts: a Uint8Array over a plain ArrayBuffer. */
export type Bytes = Uint8Array<ArrayBuffer>;

export interface KeyPair {
  /** Opaque: a CryptoKey on the WebCrypto path, raw bytes on the JavaScript path. */
  readonly privateKey: unknown;
  readonly publicKey: Bytes;
}

export interface X25519 {
  readonly name: "webcrypto" | "js";
  /** True when the private key cannot be read out by script. */
  readonly keysAreUnreadable: boolean;
  generate(): Promise<KeyPair>;
  fromPrivateBytes(raw: Bytes): Promise<KeyPair>;
  dh(privateKey: unknown, remotePublic: Bytes): Promise<Bytes>;
}

export class KeyError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "KeyError";
  }
}

// The fixed prefix of a PKCS8 X25519 private key; the 32 key bytes follow.
const PKCS8_PREFIX = Uint8Array.from([
  0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x6e, 0x04, 0x22, 0x04, 0x20,
]);

function bytes(buffer: ArrayBuffer): Bytes {
  return new Uint8Array(buffer);
}

const webCryptoImplementation: X25519 = {
  name: "webcrypto",
  keysAreUnreadable: true,
  async generate() {
    // Not extractable: this is what keeps a device key out of reach of script on the page.
    const pair = (await crypto.subtle.generateKey({ name: "X25519" }, false, [
      "deriveBits",
    ])) as CryptoKeyPair;
    return { privateKey: pair.privateKey, publicKey: bytes(await crypto.subtle.exportKey("raw", pair.publicKey)) };
  },
  async fromPrivateBytes(raw) {
    if (raw.length !== KEY_LEN) throw new KeyError("a private key is 32 bytes");
    const pkcs8 = new Uint8Array(PKCS8_PREFIX.length + KEY_LEN);
    pkcs8.set(PKCS8_PREFIX);
    pkcs8.set(raw, PKCS8_PREFIX.length);
    // Extractable only so the matching public key can be read; the key we hand back is the non-extractable one.
    const imported = await crypto.subtle.importKey("pkcs8", pkcs8, { name: "X25519" }, true, ["deriveBits"]);
    const jwk = await crypto.subtle.exportKey("jwk", imported);
    const publicKey = Uint8Array.from(atob((jwk.x ?? "").replace(/-/g, "+").replace(/_/g, "/").padEnd(44, "=")), (c) =>
      c.charCodeAt(0),
    );
    const privateKey = await crypto.subtle.importKey("pkcs8", pkcs8, { name: "X25519" }, false, ["deriveBits"]);
    return { privateKey, publicKey };
  },
  async dh(privateKey, remotePublic) {
    try {
      const peer = await crypto.subtle.importKey("raw", remotePublic, { name: "X25519" }, false, []);
      return bytes(await crypto.subtle.deriveBits({ name: "X25519", public: peer }, privateKey as CryptoKey, 256));
    } catch {
      throw new KeyError("invalid public key");
    }
  },
};

const jsImplementation: X25519 = {
  name: "js",
  keysAreUnreadable: false,
  async generate() {
    const privateKey = x25519.utils.randomSecretKey();
    return { privateKey, publicKey: x25519.getPublicKey(privateKey) };
  },
  async fromPrivateBytes(raw) {
    if (raw.length !== KEY_LEN) throw new KeyError("a private key is 32 bytes");
    const privateKey = Uint8Array.from(raw);
    return { privateKey, publicKey: x25519.getPublicKey(privateKey) };
  },
  async dh(privateKey, remotePublic) {
    try {
      return x25519.getSharedSecret(privateKey as Bytes, remotePublic);
    } catch {
      throw new KeyError("invalid public key");
    }
  },
};

/** Feature detection by doing it: generate a key, derive with itself, see that 32 bytes come out. */
export async function webCryptoSupportsX25519(): Promise<boolean> {
  try {
    if (typeof crypto === "undefined" || !crypto.subtle) return false;
    const a = await webCryptoImplementation.generate();
    const b = await webCryptoImplementation.generate();
    return (await webCryptoImplementation.dh(a.privateKey, b.publicKey)).length === KEY_LEN;
  } catch {
    return false;
  }
}

export async function getX25519(prefer: "auto" | "webcrypto" | "js" = "auto"): Promise<X25519> {
  if (prefer === "js") return jsImplementation;
  if (prefer === "webcrypto") return webCryptoImplementation;
  return (await webCryptoSupportsX25519()) ? webCryptoImplementation : jsImplementation;
}
