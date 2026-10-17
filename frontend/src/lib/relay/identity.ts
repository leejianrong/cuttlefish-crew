// What a paired browser keeps (ADR-0037, ADR-0039): its own key, the daemon's key, and the secret the daemon gave it. The private key is a
// WebCrypto key that script can use and cannot read out (where the browser has X25519; on the JavaScript fallback it is bytes, which
// `keysAreUnreadable` on the curve says). IndexedDB stores a CryptoKey as it is. The secret alone is no use to anyone: the handshake also
// needs the private key.

import type { Bytes } from "./x25519";
import { KEY_LEN } from "./x25519";

export interface DeviceIdentity {
  id: string;
  name: string;
  tier: string;
  relay: string;
  room: string;
  daemon: Bytes;
  /** The per-device secret the daemon sent when the owner confirmed. */
  secret: Bytes;
  publicKey: Bytes;
  /** A non-extractable CryptoKey on the WebCrypto path, raw bytes on the JavaScript one. */
  privateKey: unknown;
  curve: "webcrypto" | "js";
}

/** The three calls the identity needs, so tests can use a Map and the browser uses IndexedDB. */
export interface KeyValueStore {
  get(key: string): Promise<unknown>;
  set(key: string, value: unknown): Promise<void>;
  delete(key: string): Promise<void>;
}

const KEY = "identity";

export function indexedDbStore(name = "cuttlefish-relay"): KeyValueStore {
  const open = () =>
    new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open(name, 1);
      request.onupgradeneeded = () => request.result.createObjectStore("kv");
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  const run = async <T>(mode: IDBTransactionMode, work: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> => {
    const db = await open();
    try {
      return await new Promise<T>((resolve, reject) => {
        const request = work(db.transaction("kv", mode).objectStore("kv"));
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
    } finally {
      db.close();
    }
  };
  return {
    get: (key) => run("readonly", (store) => store.get(key)),
    set: async (key, value) => void (await run("readwrite", (store) => store.put(value, key))),
    delete: async (key) => void (await run("readwrite", (store) => store.delete(key))),
  };
}

function isBytes(value: unknown, length?: number): value is Bytes {
  return value instanceof Uint8Array && (length === undefined || value.length === length);
}

/** The stored identity, or null if there is none or it is not one this version wrote. A browser with blocked storage has none. */
export async function loadIdentity(store: KeyValueStore): Promise<DeviceIdentity | null> {
  let raw: unknown;
  try {
    raw = await store.get(KEY);
  } catch {
    return null;
  }
  const v = raw as Partial<DeviceIdentity> | null | undefined;
  if (
    !v ||
    typeof v.id !== "string" ||
    typeof v.name !== "string" ||
    typeof v.tier !== "string" ||
    typeof v.relay !== "string" ||
    typeof v.room !== "string" ||
    !isBytes(v.daemon, KEY_LEN) ||
    !isBytes(v.secret, KEY_LEN) ||
    !isBytes(v.publicKey, KEY_LEN) ||
    v.privateKey === undefined ||
    (v.curve !== "webcrypto" && v.curve !== "js")
  ) {
    return null;
  }
  return v as DeviceIdentity;
}

/** Throws if the browser will not keep it: a device that cannot remember its pairing should say so, not pair and forget. */
export async function saveIdentity(store: KeyValueStore, identity: DeviceIdentity): Promise<void> {
  await store.set(KEY, identity);
}

export async function clearIdentity(store: KeyValueStore): Promise<void> {
  try {
    await store.delete(KEY);
  } catch {
    // Nothing was stored.
  }
}
