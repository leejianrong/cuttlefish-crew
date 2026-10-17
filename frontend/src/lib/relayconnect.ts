// Using the relay from the dashboard (ADR-0037, ADR-0039). A page opened from a pairing link (`#pair=<payload>`) runs the pairing; a
// browser that has paired before finds its identity in IndexedDB and uses the relay from then on. The link's secret is taken out of the
// address bar at once (a fragment is never sent to a server, but a copied URL would carry it) and is kept nowhere: a reload during
// pairing asks for a new link, which is the right trade for a one-time secret.

import {
  INSECURE_PAGE_MESSAGE,
  PairingError,
  RelayTransport,
  clearIdentity,
  decodePairing,
  indexedDbStore,
  loadIdentity,
  webCryptoAvailable,
  type DeviceIdentity,
  type KeyValueStore,
  type PairingLink,
} from "./relay";

const PREFIX = "#pair=";

export type RelayStart =
  | { kind: "none" }
  | { kind: "pair"; link: PairingLink }
  | { kind: "paired"; identity: DeviceIdentity }
  | { kind: "error"; message: string };

/** IndexedDB, or nothing at all where a browser has none (a private window, blocked site data). */
export function identityStore(): KeyValueStore | null {
  try {
    return typeof indexedDB === "undefined" ? null : indexedDbStore();
  } catch {
    return null;
  }
}

export async function startRelay(
  location: Pick<Location, "hash" | "pathname" | "search"> = window.location,
): Promise<RelayStart> {
  // Read the hash before touching the address: `window.location` is live, and replaceState changes it.
  const { hash, pathname, search } = location;
  const cryptoOk = webCryptoAvailable();
  if (hash.startsWith(PREFIX)) {
    history.replaceState(null, "", `${pathname}${search}#/projects`);
    // Plain http on a Tailscale or LAN address has no WebCrypto, so say so rather than fail obscurely.
    if (!cryptoOk) return { kind: "error", message: INSECURE_PAGE_MESSAGE };
    try {
      return { kind: "pair", link: decodePairing(hash.slice(PREFIX.length)) };
    } catch (error) {
      return { kind: "error", message: error instanceof PairingError ? error.message : "This pairing link could not be read." };
    }
  }
  // A browser that paired earlier but is now on an address with no WebCrypto signs in directly instead.
  const store = cryptoOk ? identityStore() : null;
  const identity = store ? await loadIdentity(store) : null;
  return identity ? { kind: "paired", identity } : { kind: "none" };
}

export async function forgetRelay(): Promise<void> {
  const store = identityStore();
  if (store) await clearIdentity(store);
}

/** A name this device can give itself, from what the browser says it is: "Android phone, Chrome". */
export function deviceName(userAgent: string = navigator.userAgent): string {
  const os = /iPhone/.test(userAgent)
    ? "iPhone"
    : /iPad/.test(userAgent)
      ? "iPad"
      : /Android/.test(userAgent)
        ? "Android phone"
        : /Windows/.test(userAgent)
          ? "Windows PC"
          : /Mac OS X|Macintosh/.test(userAgent)
            ? "Mac"
            : /Linux/.test(userAgent)
              ? "Linux PC"
              : "Device";
  const browser = /Edg\//.test(userAgent)
    ? "Edge"
    : /Firefox\//.test(userAgent)
      ? "Firefox"
      : /Chrome\/|CriOS\//.test(userAgent)
        ? "Chrome"
        : /Safari\//.test(userAgent)
          ? "Safari"
          : "";
  return browser ? `${os}, ${browser}` : os;
}

export function makeRelayTransport(identity: DeviceIdentity, onRevoked: () => void): RelayTransport {
  return new RelayTransport(identity, onRevoked);
}
