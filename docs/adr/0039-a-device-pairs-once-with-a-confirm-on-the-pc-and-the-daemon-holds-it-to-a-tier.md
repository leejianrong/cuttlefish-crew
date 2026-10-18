# ADR-0039: A device pairs once, with a confirm on the PC, and the daemon holds it to a tier

- Status: Accepted
- Date: 2026-10-17
- Deciders: Jian (tiers and the default, ADR-0037); the choices below were made while building CUT-120 and are open to review

## Context

ADR-0037 decided the relay, the handshake, the per-device tiers and that a new device gets View and Approve. It left the
shape of pairing, the short code and what a person sees on each screen open, and it asked for an outside review of the
design before accounts and hosting ship. CUT-119 proved the path with a temporary link: one secret, new on each daemon
start, and anyone holding it had the whole dashboard. This ADR replaces that link with the real thing.

## Decision

### Pairing

1. The owner, on the PC's own dashboard (the loopback token or the password session), asks for a pairing: **Devices, Make a
   pairing link**. The daemon opens one pairing with a one-time 32-byte secret that lives for five minutes. A second request
   replaces the first, so there is one secret at a time. The dashboard shows a link and a QR code.
2. The link is `<address>#pair=<payload>`: relay address, room, the daemon's public key and the secret, in a URL
   **fragment**, which no server receives. The page takes it out of the address bar at once and keeps it nowhere.
3. The phone makes a new X25519 key (non-extractable where WebCrypto allows), connects, and handshakes with the pairing
   secret as the psk. Its first message is a ping carrying the name it chose.
4. **Only now is the secret spent**, whatever the owner decides, and only after that first message decrypted. A second
   device with the same secret is turned away. A secret nobody used is refused after five minutes.
5. The daemon holds the phone in a **waiting room**: its session may send a ping and nothing else, and a request ends the
   session. The waiting room holds at most eight devices and each waits at most five minutes.
6. Both screens show a **six-digit code** derived from the handshake hash (`protocol/pairing.py`). The phone checks the
   daemon's code against its own and stops if they differ. The owner confirms only if the codes match.
7. On **Confirm** (or Reject) the daemon stores the device, makes a fresh 32-byte secret for it and sends it inside the
   encrypted session. Every later handshake uses that secret and the device's own key. The daemon writes nothing about the
   device until the owner has said yes.

### What a photographed QR code can and cannot do

A stranger who photographed the code and connects first gets a different handshake and so a different code, sits in the
waiting room with the name they typed, and is let in only if the owner confirms. If the stranger is first, the owner's own
phone is refused (the secret is spent), the owner sees a device they do not recognise and rejects it. This relies on the owner reading the two codes, and on the code being bound to one handshake. It
defends against a stranger who is not at the PC, not against one who can also see the PC's screen.

### The device store

A SQLite file next to the daemon's key (`~/.cuttlefish/devices.db`, mode 0600): public key, name, per-device secret, tier,
created, last seen and revoked. The per-device secret is the psk, and the handshake also needs the device's private key, so
the file alone does not let anyone in. It is not encrypted at rest. `relay-key` (the daemon's own key) is mode 0600.

### Tiers

Ordered, and each includes the lower ones: **View, Approve, Terminal, Admin**. `fleet/tiers.py` names the tier every route
needs, including the websocket. **A route not in the table needs Admin**, and a unit test fails with the route's name if
one is added without a tier, or one is listed that no longer exists. The daemon checks the tier on every request through
the relay, reading it fresh each time, so a change made on the PC applies to a live session. The PC's own session is not
held to a tier.

- New devices get **Approve**. Confirm can choose View or Approve; Terminal and Admin are given afterwards, on the PC
  (or by a device that is already Admin).
- Pairing, changing a tier, settings, secrets, folders and adding or removing projects need **Admin**. The terminal needs
  **Terminal**.
- **Revoking needs only Approve**, so a lost phone can be cut off from another paired device (ADR-0037). The cost is that a
  stolen unlocked Approve phone can remove the owner's other devices; it cannot pair a new one.
- A refusal is a 403 with a sentence a person can act on ("This device is paired as approve and this needs admin. Change it
  under Devices on the PC."), a websocket close 4403 for the terminal, and the dashboard shows that sentence.

### How the daemon knows which device

The connector, not the browser, says which device a request is from: it drops any `x-cuttlefish-token`,
`x-cuttlefish-device` or `Host` the browser sent and sets its own, and for a terminal it swaps the token and adds the device
in the first websocket message. The token is made per process and is known only to the connector and the daemon's check.
A test sends a header naming a more powerful device and still gets a 403.

### Revocation

`DELETE /api/devices/{id}` marks the device revoked, sends it `{"t": "revoked"}`, and cuts its live sessions at once. The
next handshake finds no secret for its key and is refused before any message is read. A device that removes itself
(Disconnect on a paired phone) does the same to itself.

### Protocol additions

A `CONTROL` frame (type 11, stream 0, JSON) carries pairing progress and the revocation notice. It is in the Python and
TypeScript packages with a vector test for the short code in both.

## Found after it was written: pairing needs a secure origin

The handshake, the keys and the session all use `crypto.subtle`, which every browser withholds from a page that is not a
secure context: https, or http on localhost. Opened at `http://<tailscale ip>:<port>`, `isSecureContext` is false and
`crypto.subtle` is undefined (checked in Chromium and Firefox on a real Tailscale address). So **a phone cannot pair from the
plain-http Tailscale dashboard**. The earlier runs of this ADR's flow all used `127.0.0.1`, which is exempt, and so did not show it.
The dashboard now says so on both sides (the Devices screen warns about the address it will put in the link, and a page that
cannot pair explains why and leaves the direct password sign-in available). The ways forward are the https relay (CUT-121,
which serves the page), https addresses from Tailscale, or a pure-JavaScript fallback for the crypto that would work on any
origin and would give up the "the code came over tls" argument ADR-0037 makes for a hosted page. Jian chose, on 2026-10-18: Tailscale
https first, the hosted relay second, and the JavaScript fallback dropped (ADR-0041).

## Left out on purpose

- **A WebAuthn step-up for Terminal and Admin** (ADR-0037). It needs a secure origin, and a page served over plain http on
  Tailscale or loopback-by-IP is not one. Until the https relay (CUT-121) there is no step-up: Terminal and Admin are off
  until the owner turns them on for that device, and they stay on until the owner turns them off.
- **Key rotation, a lost daemon key, and a device's own key being replaced.** Re-pairing is the way: remove the device and
  pair it again.
- **An audit log of pairing and revocation** beyond the daemon's own log lines.
- **A confirm from a paired device.** Only the PC's own session confirms, so a stolen phone cannot add another.

## Consequences

- A stolen unlocked phone is a paired device at its tier until the owner removes it. At Approve it can answer Needs you,
  steer and start or stop teams. Removing it is one tap on the PC or on another paired device.
- The outside review ADR-0037 asks for has a concrete thing to review now: this ADR, `fleet/tiers.py`, `fleet/devices.py`,
  `relay/connector.py`, and the tests under `tests/integration/relay/`. **Accounts and hosting still wait for it.**
- The temporary `#relay=` link of CUT-119 is gone.

## Alternatives considered

| Option | Why not |
|---|---|
| Confirm on the phone only (a passcode) | A photographed QR would then be enough with a guessed code. The PC is the owner's anchor. |
| Several open pairings at once | The responder must pick a psk before it knows which the caller holds, so each would need its own trial handshake. One at a time is simpler and enough. |
| A longer code or a word list | Six digits are read aloud and compared easily; the code defends against a stranger who is not at the PC, not a brute force, because it is bound to one handshake. |
| Store the secret hashed | The psk is used as key material by the handshake, so it must be stored in a form that can be used. |
| Tier as a bit set per device | Terminal without Approve is not a case anyone wants, and an ordered level is simpler to explain and to review. |

## Checked

Python integration tests with a real relay, connector and daemon app: the whole pairing with matching codes, nothing served
in the waiting room, a photographed code pairing nobody, a secret working once and expiring, a wait expiring, tiers across
every kind of route with sentences a person can act on, a spoofed device header, the terminal needing the Terminal tier,
live tier changes, revocation cutting a live session and refusing the next handshake, a paired device revoking another, and
a View device unable to. Four properties were broken on purpose (no tier check, a secret not spent, a revoked device keeping
its secret, a waiting room that serves) and each made tests fail. Chromium at 390px against a real daemon: the pairing, the
codes matching, a refused terminal and refused settings with the daemon's sentence, the tier raised and the terminal working,
a reload staying paired, and removal telling the phone. Not run: Safari and Firefox, a real phone, and the outside review.
