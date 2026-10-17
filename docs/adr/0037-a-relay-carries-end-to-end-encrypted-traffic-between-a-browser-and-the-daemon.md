# ADR-0037: A relay carries end-to-end encrypted traffic between a browser and the daemon, and lives in this repo as its own package

- Status: Proposed (a spike's draft, to be accepted or changed by Jian before any relay code)
- Date: 2026-10-17
- Deciders: Jian

## Context

The goal is a product other people can use: a public web app where a person signs in and sees a dashboard
connected to their PC, a local daemon on that PC, and a phone or laptop that steers what runs there, with no VPN to
install. ADR-0032 said "a public relay comes later". ADR-0013 serves a private network over plain http, which a
browser treats as an insecure origin: no service worker, no app badge and no notification permission (measured in
CUT-98's investigation). A PC behind a home router accepts no inbound connection, so something public has to meet
the browser and the PC's own outbound connection. That something is a relay.

Jian's decisions, 2026-10-17: the relay is a near-term goal; it is built in this repository; the encryption is end to
end and strong, even at the cost of a bigger build.

The hands-on checks are in `docs/research/relay-spike.md`, with a prototype in `docs/research/relay-spike/`.

## Decision

**A relay passes encrypted frames between a browser and a daemon and cannot read them.** The daemon dials out to it,
a browser connects to it, and a Noise handshake between those two ends sets the keys. Only the two ends can read what
crosses it.

### Layout, in this repository

- `cuttlefish.relay.protocol`: the handshake and the framing, shared by both ends.
- `cuttlefish.relay.server`: the relay service (rooms, accounts later). Deployed on its own.
- `cuttlefish.relay.connector`: the daemon's side.
- `frontend/src/lib/relay/`: the browser's side.

`relay.server` must not import the fleet, secrets or episodic modules, and a test enforces it. The relay holds no
project data. The daemon imports `protocol` and `connector` only. Splitting into another repository later is a move,
because the protocol is a package with no dependency on the rest.

### Keys and pairing

- The daemon has a long-term X25519 key on its disk. A browser creates its own with WebCrypto as a **non-extractable**
  key kept in IndexedDB. Script on the page can use it and cannot copy it out (checked in the spike).
- The handshake is **`Noise_IKpsk2_25519_AESGCM_SHA256`**. IK suits this because the browser already knows the daemon's
  key from the pairing code, so a session takes one round trip, both sides prove their key, and every session has
  forward secrecy. The psk adds a second secret that a person without it cannot guess.
- **Pairing code (QR or link):** relay address, room id, the daemon's public key, a one-time 32-byte secret, a five
  minute expiry. The secret is the psk for the first handshake only and is single use.
- **Confirmation on the PC.** The daemon shows the new device's name and a short code derived from the handshake
  hash. Both screens show it, and the person confirms on the PC. A photographed QR does not pair a stranger without
  that confirmation.
- **After confirmation** the daemon stores the device (public key, name, created, last seen, revoked) and sends a
  fresh 32-byte per-device secret in its first encrypted message. Every later session uses that as the psk.
- **A device counts as authenticated only when the daemon has decrypted its first valid transport message.** Noise
  gives the responder no proof before that, and the spike showed a wrong-psk client reaching "handshake done" on
  the daemon's side.
- **Revocation** from the PC and from any other paired device. A revoked key is refused at the handshake.

### Accounts and devices are separate

A sign-in (a passkey, once accounts are designed) lets a browser reach the relay and list which of that account's
PCs are online. It grants no access to any PC. Each browser has to be paired with each PC. A stolen account password
exposes which PCs exist and when, not what they do.

### Wire format

- One websocket binary frame is one Noise transport message, **at most 65535 bytes** (the Noise limit, found in the
  spike).
- A decrypted frame is one type byte, a four-byte stream id and a payload. Types: request, response, data, end,
  websocket open, websocket data, websocket close, ping.
- Many streams share one connection, so a slow download does not block the terminal. Bodies are cut into chunks of
  60000 bytes.
- The spike measured a handshake of 7 to 15 ms and bulk transfer of 15 to 20 MiB/s on loopback with binary frames, so
  encryption is not the limit.

### How the dashboard uses it

`frontend/src/lib/api.ts` has four `fetch` calls and `TerminalView.svelte` has the one `WebSocket`. Both go through an
injected **transport**. The same-origin transport stays for loopback and Tailscale. The relay transport sends the same
requests as streams. Nothing else in the UI changes.

On the daemon, a request from the connector enters the daemon's own application in the same process. It is checked by
a third `SecurityCheck` (alongside the loopback token and the password session of ADR-0011) that accepts an
authenticated, non-revoked device, and carries that device's capability tier.

### Capability tiers per device

| Tier | What it allows |
|---|---|
| View | Read the dashboard, Needs you, Artifacts, activity |
| Approve | Answer Needs you cards, steer, start and stop teams and sessions |
| Terminal | The agent's terminal, and the shell (a later ADR) |
| Admin | Settings, roles, secrets (values stay sealed), adding projects, pairing and revoking devices |

A new device gets **View and Approve**. **Terminal** and **Admin** need a step-up on the phone (a WebAuthn user
verification, valid for a short window) and are off until the PC owner turns them on for that device. The tier is
enforced by the daemon, never by the relay or the browser.

### What the relay can see, said plainly

It sees which accounts and devices are online, when they connect, how many bytes move and how often. It does not
see paths, commands, output, the terminal, artifacts or secrets.

**The limit.** In a browser the code that does the encrypting arrives from a server. A relay that was compromised,
or run by someone hostile, could serve changed code that steals a session. End-to-end encryption here protects
against a database leak, logs, a network attacker and a curious operator. It does not fully protect against an
actively malicious server. We say that in the product and reduce it with a small, open-source loader, subresource
integrity, non-extractable keys, a published hash of the shipped bundle and, later, an installable app with a pinned
bundle. A person who cannot accept it can use the Tailscale path (ADR-0013), where no third party serves the code.

### A mobile app is a later client, not a different design

Jian is open to a cross-platform app if the browser proves hard. The protocol does not care which client speaks it:
Noise, X25519 and AES-GCM exist for Swift, Kotlin, Dart and JavaScript, and the frames are plain bytes. A native
client would close the browser's one real gap: the code is installed and signed, so a relay cannot swap it. It also
gets keys in the Keychain or Keystore, push through APNs and FCM without a Home Screen install on iOS, and a real
badge. The cost is two store listings and review, and a second client. Three routes, cheapest first:

1. **The browser first (this ADR).** Works today, nothing to install, and the spike ran in Chromium.
2. **The same Svelte build in a native shell (Capacitor or similar).** Pinned bundle, secure storage and native push
   for little extra code, and the dashboard is not rewritten. This is the route to take when the browser's limits
   hurt.
3. **A Flutter or React Native rewrite.** Best native feel, and it means rebuilding every dashboard screen and the
   terminal in a second codebase. I would not start here.

Nothing in this ADR has to change to allow 2 or 3. The transport is already a seam, and the capability tiers and
pairing are enforced by the daemon.

### Notifications and media

- The relay is https, so it can register Web Push and an app badge. A push carries a count and a project name. Detail
  is fetched over the encrypted channel when the app opens. Content encrypted for the device, decrypted in the
  service worker, is possible later.
- Images and video are compressed on the PC before they leave it (`ffmpeg`, optional), with a size cap.

## Alternatives considered

| Option | Why not |
|---|---|
| Tailscale only | Stays for people who want it. A public web app for people with no VPN needs more. |
| A TLS-terminating tunnel (Cloudflare Tunnel, frp) | Quick, but the operator sees every byte, which Jian ruled out. Fine as a user-owned path. |
| WebRTC peer to peer | Needs a signalling server and a TURN relay for the failures, so the server is built anyway, and the browser still gets its code from a server. |
| Our own use of libsodium sealed boxes | Fewer steps, but no forward secrecy and no mutual proof, and we would be designing a protocol. |
| Noise XX | Two round trips and no known key up front, so the pairing code gives it nothing to pin. |
| Signal's double ratchet or MLS | Built for asynchronous messages and groups. Far more than one browser to one daemon needs. |
| `sshd` on the PC | Needs an open port, host and key management, and does not work behind a router. |

## Consequences

- Needs its own service to run, with accounts, rate limits and abuse handling that the self-hosted path never needed.
- A new, security-critical area: the protocol is small and uses Noise, but the pairing flow, the tiers and the
  revocation path need review before release. Independent review is required, not optional.
- The daemon grows a connector, a device store, a third `SecurityCheck` and a pairing screen.
- Work on a shell, previews and media later rides on the tiers and streams defined here.
- ADR-0011 and ADR-0013 stand for loopback and Tailscale. ADR-0001 to ADR-0036 are unaffected.

## Not decided here

- The account provider, billing and where the relay is hosted.
- The password or passkey flow for the relay account, and recovery.
- The exact short-code format and what a person sees on each screen.
- Key rotation inside a long session, and what happens when a PC's key is lost.
- How Safari's WebCrypto timeline affects the fallback. The client should feature-detect and carry a pure
  JavaScript implementation.
- Whether to publish a hash of the shipped bundle and who verifies it.

## Verification that remains

Only Chromium was run (`docs/research/relay-spike.md` lists the rest). Before this is accepted: run the prototype
in Safari and Firefox, measure over a real mobile connection, and have the pairing design reviewed.
