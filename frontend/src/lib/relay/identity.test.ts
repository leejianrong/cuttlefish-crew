import { describe, expect, it } from "vitest";
import { clearIdentity, loadIdentity, saveIdentity, type DeviceIdentity, type KeyValueStore } from "./identity";

function memory(): KeyValueStore & { data: Map<string, unknown> } {
  const data = new Map<string, unknown>();
  return {
    data,
    get: async (k) => data.get(k),
    set: async (k, v) => void data.set(k, v),
    delete: async (k) => void data.delete(k),
  };
}

const identity = (): DeviceIdentity => ({
  id: "d1",
  name: "Phone",
  tier: "approve",
  relay: "ws://127.0.0.1:8450",
  room: "room",
  daemon: new Uint8Array(32).fill(1),
  secret: new Uint8Array(32).fill(2),
  publicKey: new Uint8Array(32).fill(3),
  privateKey: { opaque: true },
  curve: "webcrypto",
});

describe("identity", () => {
  it("round-trips, and clears", async () => {
    const store = memory();
    expect(await loadIdentity(store)).toBeNull();
    await saveIdentity(store, identity());
    expect(await loadIdentity(store)).toEqual(identity());
    await clearIdentity(store);
    expect(await loadIdentity(store)).toBeNull();
  });

  it("treats anything it did not write as no identity at all", async () => {
    const store = memory();
    for (const bad of [
      "text",
      { ...identity(), secret: new Uint8Array(5) },
      { ...identity(), curve: "other" },
      { ...identity(), privateKey: undefined },
      { ...identity(), id: 7 },
    ]) {
      store.data.set("identity", bad);
      expect(await loadIdentity(store)).toBeNull();
    }
  });

  it("is no identity, not a crash, when storage itself fails", async () => {
    const broken: KeyValueStore = {
      get: async () => {
        throw new Error("blocked");
      },
      set: async () => {},
      delete: async () => {
        throw new Error("blocked");
      },
    };
    expect(await loadIdentity(broken)).toBeNull();
    await expect(clearIdentity(broken)).resolves.toBeUndefined();
  });
});
