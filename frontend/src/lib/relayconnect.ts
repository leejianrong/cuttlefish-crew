// Opening the dashboard through a relay (ADR-0037, CUT-119). The daemon prints a link that ends in `#relay=<payload>`; this
// reads it once, remembers it for this tab only, and takes it out of the address bar so a copied URL does not carry the secret
// on. A fragment is never sent to any server. Config-only pairing: CUT-120 replaces the payload with a one-time pairing.

import { PairingError, RelayTransport, decodePairing, type RelayConfig } from "./relay";

const STORAGE_KEY = "cuttlefish.relay.pairing";
const PREFIX = "#relay=";

export type RelayStart = { transport: RelayTransport; room: string } | { error: string } | null;

function remember(payload: string | null): string | null {
  try {
    if (payload) sessionStorage.setItem(STORAGE_KEY, payload);
    return payload ?? sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return payload;
  }
}

/** A relay transport if this page was opened from a pairing link (or reloaded in a tab that was), else null. */
export function startRelay(location: Pick<Location, "hash" | "pathname" | "search"> = window.location): RelayStart {
  const fromHash = location.hash.startsWith(PREFIX) ? location.hash.slice(PREFIX.length) : null;
  const payload = remember(fromHash);
  if (fromHash !== null) history.replaceState(null, "", `${location.pathname}${location.search}#/projects`);
  if (!payload) return null;
  let config: RelayConfig;
  try {
    config = decodePairing(payload);
  } catch (error) {
    forgetRelay();
    return { error: error instanceof PairingError ? error.message : "This pairing link could not be read." };
  }
  return { transport: new RelayTransport(config), room: config.room };
}

export function forgetRelay(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Blocked site data: nothing was kept.
  }
}
