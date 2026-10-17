// Unit: the browser's side of the Noise handshake (ADR-0037), on both X25519 paths.
// The vectors come from an implementation we did not write, so a misreading of the specification cannot hide behind
// a matching misreading in the Python end.

import { describe, expect, it } from "vitest";
import { Initiator, MAX_MESSAGE_LEN, NoiseError, TAG_LEN, type Transport } from "./noise";
import { hex, toHex, vectors } from "./vectors";
import { getX25519, webCryptoSupportsX25519, type Bytes, type X25519 } from "./x25519";

const paths: ("webcrypto" | "js")[] = ["webcrypto", "js"];

async function start(curve: X25519, overrides: { psk?: Bytes; remoteStatic?: Bytes } = {}) {
  const staticKey = await curve.fromPrivateBytes(hex(vectors.initiator_static_private));
  const ephemeral = await curve.fromPrivateBytes(hex(vectors.initiator_ephemeral_private));
  return Initiator.create(
    staticKey,
    overrides.remoteStatic ?? hex(vectors.responder_static_public),
    overrides.psk ?? hex(vectors.psk),
    { prologue: hex(vectors.prologue), ephemeral, x25519: curve },
  );
}

describe.each(paths)("handshake against the independent vectors (%s)", (path) => {
  it("derives the public keys the vectors list", async () => {
    const curve = await getX25519(path);
    const key = await curve.fromPrivateBytes(hex(vectors.initiator_static_private));
    expect(toHex(key.publicKey)).toBe(vectors.initiator_static_public);
  });

  it("writes the same first message byte for byte", async () => {
    const initiator = await start(await getX25519(path));
    expect(toHex(await initiator.writeMessage1(hex(vectors.payload1)))).toBe(vectors.message1);
  });

  it("finishes on the other implementation's second message and reads its payload", async () => {
    const initiator = await start(await getX25519(path));
    await initiator.writeMessage1(hex(vectors.payload1));
    const { transport, payload } = await initiator.readMessage2(hex(vectors.message2));
    expect(toHex(payload)).toBe(vectors.payload2);
    expect(toHex(transport.handshakeHash)).toBe(vectors.handshake_hash);
  });

  it("encrypts and decrypts every transport message in the vectors", async () => {
    const initiator = await start(await getX25519(path));
    await initiator.writeMessage1(hex(vectors.payload1));
    const { transport } = await initiator.readMessage2(hex(vectors.message2));
    for (const item of vectors.transport) {
      if (item.from === "initiator") {
        expect(toHex(await transport.encrypt(hex(item.plaintext)))).toBe(item.ciphertext);
      } else {
        expect(toHex(await transport.decrypt(hex(item.ciphertext)))).toBe(item.plaintext);
      }
    }
  });
});

describe("what must be refused", () => {
  async function established(): Promise<Transport> {
    const initiator = await start(await getX25519("webcrypto"));
    await initiator.writeMessage1(hex(vectors.payload1));
    return (await initiator.readMessage2(hex(vectors.message2))).transport;
  }

  it("a second message made with another psk", async () => {
    const initiator = await start(await getX25519("webcrypto"), { psk: new Uint8Array(32) });
    await initiator.writeMessage1(hex(vectors.payload1));
    await expect(initiator.readMessage2(hex(vectors.message2))).rejects.toBeInstanceOf(NoiseError);
  });

  it("a psk that is not 32 bytes", async () => {
    await expect(start(await getX25519("webcrypto"), { psk: new Uint8Array(5) })).rejects.toBeInstanceOf(NoiseError);
  });

  it("a tampered second message", async () => {
    const initiator = await start(await getX25519("webcrypto"));
    await initiator.writeMessage1(hex(vectors.payload1));
    const message = hex(vectors.message2);
    message[message.length - 1] ^= 1;
    await expect(initiator.readMessage2(message)).rejects.toBeInstanceOf(NoiseError);
  });

  it("a short or low-order second message", async () => {
    const initiator = await start(await getX25519("webcrypto"));
    await initiator.writeMessage1(hex(vectors.payload1));
    await expect(initiator.readMessage2(new Uint8Array(10))).rejects.toBeInstanceOf(NoiseError);
    await expect(initiator.readMessage2(new Uint8Array(64))).rejects.toBeInstanceOf(NoiseError);
  });

  it("message order: no reply before the first message, and no first message twice", async () => {
    const initiator = await start(await getX25519("webcrypto"));
    await expect(initiator.readMessage2(hex(vectors.message2))).rejects.toBeInstanceOf(NoiseError);
    await initiator.writeMessage1();
    await expect(initiator.writeMessage1()).rejects.toBeInstanceOf(NoiseError);
  });

  it("a tampered transport message, which also ends the session", async () => {
    const transport = await established();
    const item = vectors.transport.find((t) => t.from === "responder")!;
    // The first transport message the initiator received in the vectors is the responder's, at counter 0.
    const cipher = hex(item.ciphertext);
    cipher[0] ^= 1;
    await expect(transport.decrypt(cipher)).rejects.toBeInstanceOf(NoiseError);
    await expect(transport.decrypt(hex(item.ciphertext))).rejects.toBeInstanceOf(NoiseError);
    await expect(transport.encrypt(new Uint8Array(1))).rejects.toBeInstanceOf(NoiseError);
  });

  it("a replayed transport message", async () => {
    const transport = await established();
    const item = vectors.transport.find((t) => t.from === "responder")!;
    await transport.decrypt(hex(item.ciphertext));
    await expect(transport.decrypt(hex(item.ciphertext))).rejects.toBeInstanceOf(NoiseError);
  });

  it("the largest message fits and one more byte does not", async () => {
    const transport = await established();
    const biggest = new Uint8Array(MAX_MESSAGE_LEN - TAG_LEN);
    expect((await transport.encrypt(biggest)).length).toBe(MAX_MESSAGE_LEN);
    await expect(transport.encrypt(new Uint8Array(MAX_MESSAGE_LEN - TAG_LEN + 1))).rejects.toBeInstanceOf(NoiseError);
  });

  it("a session ends before its nonce would wrap", async () => {
    const transport = await established();
    transport.setSentCountForTest(2n ** 64n - 2n);
    await transport.encrypt(new Uint8Array(1));
    await expect(transport.encrypt(new Uint8Array(1))).rejects.toBeInstanceOf(NoiseError);
  });
});

describe("a fresh handshake without fixed keys", () => {
  it.each(paths)("generates its own ephemeral and static keys (%s)", async (path) => {
    const curve = await getX25519(path);
    const staticKey = await curve.generate();
    const initiator = await Initiator.create(staticKey, hex(vectors.responder_static_public), hex(vectors.psk), { x25519: curve });
    const first = await initiator.writeMessage1(new Uint8Array(0));
    expect(first.length).toBe(32 + 32 + TAG_LEN + TAG_LEN);
  });

  it("detects whether WebCrypto has X25519 and falls back to JavaScript when told it does not", async () => {
    expect(typeof (await webCryptoSupportsX25519())).toBe("boolean");
    expect((await getX25519("js")).name).toBe("js");
    expect((await getX25519("js")).keysAreUnreadable).toBe(false);
    const auto = await getX25519("auto");
    expect(auto.name).toBe((await webCryptoSupportsX25519()) ? "webcrypto" : "js");
  });
});
