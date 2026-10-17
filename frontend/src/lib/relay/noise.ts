// Noise_IKpsk2_25519_AESGCM_SHA256, the browser's (initiator's) side (ADR-0037). Hashing, HMAC and AES-GCM come from
// WebCrypto; X25519 from ./x25519 (WebCrypto, or JavaScript where it is missing). The daemon's side is Python
// (cuttlefish.relay.protocol.noise); both are checked against vectors made by an independent implementation.

import { getX25519, KeyError, type Bytes, type KeyPair, type X25519 } from "./x25519";

export const PROTOCOL_NAME = "Noise_IKpsk2_25519_AESGCM_SHA256";
export const PROLOGUE = new TextEncoder().encode("cuttlefish-relay/1");
export const KEY_LEN = 32;
export const TAG_LEN = 16;
/** The Noise limit for one message, handshake or transport. */
export const MAX_MESSAGE_LEN = 65535;
const MAX_NONCE = 2n ** 64n - 2n;

export class NoiseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NoiseError";
  }
}

const NAME = new TextEncoder().encode(PROTOCOL_NAME);

function cat(...parts: Bytes[]): Bytes {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

const sha256 = async (data: Bytes) => new Uint8Array(await crypto.subtle.digest("SHA-256", data));

async function hmac(key: Bytes, data: Bytes): Promise<Bytes> {
  const imported = await crypto.subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", imported, data));
}

async function hkdf2(chainingKey: Bytes, material: Bytes): Promise<[Bytes, Bytes]> {
  const temp = await hmac(chainingKey, material);
  const out1 = await hmac(temp, Uint8Array.of(1));
  const out2 = await hmac(temp, cat(out1, Uint8Array.of(2)));
  return [out1, out2];
}

async function hkdf3(
  chainingKey: Bytes,
  material: Bytes,
): Promise<[Bytes, Bytes, Bytes]> {
  const temp = await hmac(chainingKey, material);
  const out1 = await hmac(temp, Uint8Array.of(1));
  const out2 = await hmac(temp, cat(out1, Uint8Array.of(2)));
  const out3 = await hmac(temp, cat(out2, Uint8Array.of(3)));
  return [out1, out2, out3];
}

function nonce(counter: bigint): Bytes {
  const out = new Uint8Array(12);
  new DataView(out.buffer).setBigUint64(4, counter);
  return out;
}

async function aesKey(raw: Bytes, usage: "encrypt" | "decrypt"): Promise<CryptoKey> {
  return crypto.subtle.importKey("raw", raw, "AES-GCM", false, [usage]);
}

class Symmetric {
  h: Bytes = NAME;
  ck: Bytes = NAME;
  k: Bytes | null = null;
  n = 0n;

  async mixHash(data: Bytes): Promise<void> {
    this.h = await sha256(cat(this.h, data));
  }

  async mixKey(material: Bytes): Promise<void> {
    const [ck, k] = await hkdf2(this.ck, material);
    this.ck = ck;
    this.k = k;
    this.n = 0n;
  }

  async mixKeyAndHash(material: Bytes): Promise<void> {
    const [ck, tempH, k] = await hkdf3(this.ck, material);
    this.ck = ck;
    await this.mixHash(tempH);
    this.k = k;
    this.n = 0n;
  }

  async encryptAndHash(plain: Bytes): Promise<Bytes> {
    if (!this.k) {
      await this.mixHash(plain);
      return plain;
    }
    const key = await aesKey(this.k, "encrypt");
    const cipher = new Uint8Array(
      await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce(this.n++), additionalData: this.h, tagLength: 128 }, key, plain),
    );
    await this.mixHash(cipher);
    return cipher;
  }

  async decryptAndHash(cipher: Bytes): Promise<Bytes> {
    if (!this.k) {
      await this.mixHash(cipher);
      return cipher;
    }
    const key = await aesKey(this.k, "decrypt");
    let plain: Bytes;
    try {
      plain = new Uint8Array(
        await crypto.subtle.decrypt({ name: "AES-GCM", iv: nonce(this.n++), additionalData: this.h, tagLength: 128 }, key, cipher),
      );
    } catch {
      throw new NoiseError("handshake message failed verification");
    }
    await this.mixHash(cipher);
    return plain;
  }
}

/** The keys for the session after the handshake. A message that fails verification ends it. */
export class Transport {
  private sent = 0n;
  private received = 0n;
  private broken = false;
  private sendKey: CryptoKey | null = null;
  private recvKey: CryptoKey | null = null;

  constructor(
    private readonly sendRaw: Bytes,
    private readonly recvRaw: Bytes,
    readonly handshakeHash: Bytes,
  ) {}

  async encrypt(plain: Bytes): Promise<Bytes> {
    if (this.broken) throw new NoiseError("the session has ended");
    if (plain.length + TAG_LEN > MAX_MESSAGE_LEN) throw new NoiseError("message too large for one frame");
    if (this.sent > MAX_NONCE) throw new NoiseError("the session has sent as many messages as its key allows");
    this.sendKey ??= await aesKey(this.sendRaw, "encrypt");
    const counter = this.sent++;
    return new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce(counter), tagLength: 128 }, this.sendKey, plain));
  }

  async decrypt(cipher: Bytes): Promise<Bytes> {
    if (this.broken) throw new NoiseError("the session has ended");
    if (this.received > MAX_NONCE) {
      this.broken = true;
      throw new NoiseError("the session has received as many messages as its key allows");
    }
    this.recvKey ??= await aesKey(this.recvRaw, "decrypt");
    try {
      const plain = new Uint8Array(
        await crypto.subtle.decrypt({ name: "AES-GCM", iv: nonce(this.received), tagLength: 128 }, this.recvKey, cipher),
      );
      this.received++;
      return plain;
    } catch {
      this.broken = true;
      throw new NoiseError("message failed verification, the session has ended");
    }
  }

  /** For tests of the nonce limit only. */
  setSentCountForTest(count: bigint): void {
    this.sent = count;
  }
}

export interface InitiatorOptions {
  prologue?: Bytes;
  /** A fixed ephemeral key, for test vectors only. */
  ephemeral?: KeyPair;
  /** Which X25519 to use; the default asks the browser and falls back to JavaScript. */
  x25519?: X25519;
}

/** The browser's side of the handshake. One use per handshake. */
export class Initiator {
  private readonly sym = new Symmetric();
  private written = false;
  private ephemeral: KeyPair | null;

  private constructor(
    private readonly curve: X25519,
    private readonly staticKey: KeyPair,
    private readonly remoteStatic: Bytes,
    private readonly psk: Bytes,
    private readonly prologue: Bytes,
    ephemeral: KeyPair | null,
  ) {
    this.ephemeral = ephemeral;
  }

  static async create(
    staticKey: KeyPair,
    remoteStatic: Bytes,
    psk: Bytes,
    options: InitiatorOptions = {},
  ): Promise<Initiator> {
    if (psk.length !== KEY_LEN) throw new NoiseError("the psk must be 32 bytes");
    const curve = options.x25519 ?? (await getX25519());
    const initiator = new Initiator(curve, staticKey, remoteStatic, psk, options.prologue ?? PROLOGUE, options.ephemeral ?? null);
    await initiator.sym.mixHash(initiator.prologue);
    await initiator.sym.mixHash(remoteStatic);
    return initiator;
  }

  private async dh(privateKey: unknown, publicKey: Bytes): Promise<Bytes> {
    try {
      return await this.curve.dh(privateKey, publicKey);
    } catch (error) {
      if (error instanceof KeyError) throw new NoiseError(error.message);
      throw error;
    }
  }

  async writeMessage1(payload: Bytes = new Uint8Array(0)): Promise<Bytes> {
    if (this.written) throw new NoiseError("message 1 was already written");
    this.written = true;
    const sym = this.sym;
    this.ephemeral ??= await this.curve.generate();
    const e = this.ephemeral;
    await sym.mixHash(e.publicKey);
    await sym.mixKey(e.publicKey);
    await sym.mixKey(await this.dh(e.privateKey, this.remoteStatic));
    const encryptedStatic = await sym.encryptAndHash(this.staticKey.publicKey);
    await sym.mixKey(await this.dh(this.staticKey.privateKey, this.remoteStatic));
    const message = cat(e.publicKey, encryptedStatic, await sym.encryptAndHash(payload));
    if (message.length > MAX_MESSAGE_LEN) throw new NoiseError("handshake payload too large");
    return message;
  }

  async readMessage2(message: Bytes): Promise<{ transport: Transport; payload: Bytes }> {
    if (!this.written || !this.ephemeral) throw new NoiseError("message 1 has not been written");
    if (message.length < KEY_LEN + TAG_LEN || message.length > MAX_MESSAGE_LEN) {
      throw new NoiseError("malformed handshake message");
    }
    const sym = this.sym;
    const remoteEphemeral = message.slice(0, KEY_LEN);
    await sym.mixHash(remoteEphemeral);
    await sym.mixKey(remoteEphemeral);
    await sym.mixKey(await this.dh(this.ephemeral.privateKey, remoteEphemeral));
    await sym.mixKey(await this.dh(this.staticKey.privateKey, remoteEphemeral));
    await sym.mixKeyAndHash(this.psk);
    const payload = await sym.decryptAndHash(message.slice(KEY_LEN));
    const [first, second] = await hkdf2(sym.ck, new Uint8Array(0));
    return { transport: new Transport(first, second, sym.h), payload };
  }
}
