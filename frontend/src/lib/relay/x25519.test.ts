// Unit: the two X25519 paths agree, and the WebCrypto one keeps its key unreadable (ADR-0037).

import { describe, expect, it } from "vitest";
import { getX25519, KeyError } from "./x25519";
import { hex, toHex, vectors } from "./vectors";

describe("x25519", () => {
  it("both paths derive the same shared secret from the same keys", async () => {
    const web = await getX25519("webcrypto");
    const js = await getX25519("js");
    const a = hex(vectors.initiator_static_private);
    const b = hex(vectors.initiator_ephemeral_private);
    const [aWeb, bWeb] = [await web.fromPrivateBytes(a), await web.fromPrivateBytes(b)];
    const [aJs, bJs] = [await js.fromPrivateBytes(a), await js.fromPrivateBytes(b)];
    expect(toHex(aWeb.publicKey)).toBe(toHex(aJs.publicKey));
    const viaWeb = await web.dh(aWeb.privateKey, bWeb.publicKey);
    const viaJs = await js.dh(aJs.privateKey, bJs.publicKey);
    expect(toHex(viaWeb)).toBe(toHex(viaJs));
    expect(toHex(await web.dh(bWeb.privateKey, aWeb.publicKey))).toBe(toHex(viaWeb));
  });

  it("a generated WebCrypto key cannot be exported by script", async () => {
    const web = await getX25519("webcrypto");
    const key = await web.generate();
    await expect(crypto.subtle.exportKey("pkcs8", key.privateKey as CryptoKey)).rejects.toThrow();
    await expect(crypto.subtle.exportKey("jwk", key.privateKey as CryptoKey)).rejects.toThrow();
    expect(web.keysAreUnreadable).toBe(true);
  });

  it("a key made from bytes is not extractable either", async () => {
    const web = await getX25519("webcrypto");
    const key = await web.fromPrivateBytes(hex(vectors.initiator_static_private));
    expect((key.privateKey as CryptoKey).extractable).toBe(false);
  });

  it.each(["webcrypto", "js"] as const)("a low-order public key is refused (%s)", async (path) => {
    const curve = await getX25519(path);
    const key = await curve.generate();
    await expect(curve.dh(key.privateKey, new Uint8Array(32))).rejects.toBeInstanceOf(KeyError);
  });

  it("a private key that is not 32 bytes is refused", async () => {
    for (const path of ["webcrypto", "js"] as const) {
      await expect((await getX25519(path)).fromPrivateBytes(new Uint8Array(31))).rejects.toBeInstanceOf(KeyError);
    }
  });
});
