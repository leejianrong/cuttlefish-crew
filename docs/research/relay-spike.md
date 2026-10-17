# Relay spike (CUT-117)

Hands-on checks behind ADR-0037. Done on 2026-10-17 with the prototype in `relay-spike/`. It is throwaway code, not
part of the package (ruff excludes it). `relay-spike/run.sh` reproduces the run.

## What was built

A blind relay, a daemon-side connector and a browser page, wired as the ADR proposes, in a scratch directory:

- `noise_ikpsk2.py`: `Noise_IKpsk2_25519_AESGCM_SHA256` written from the Noise spec (revision 34) with `cryptography`.
- `noise.js`: the initiator side in WebCrypto only. No library.
- `relay.py`: pairs one `daemon` and one `client` websocket per room and forwards binary frames. It logs every
  frame and searches it for plaintext markers.
- `connector.py`: dials out to the relay, answers the handshake, then serves encrypted requests by calling a real
  `cuttlefish serve` on loopback.
- `page.html` and `driver.mjs`: Playwright drives Chromium 153 through the handshake and some requests.

## Results

1. **The handshake is correct against an independent implementation.** `check_vs_ref.py` runs my Python responder
   against the `noiseprotocol` package's initiator and my initiator against its responder, with the handshake and
   transport traffic in both directions. A wrong psk is rejected. That checks my code against the spec as another
   author read it, not just against my own JavaScript.
2. **Chromium's WebCrypto agrees with Python.** X25519 `deriveBits`, HKDF built from HMAC-SHA-256 and AES-GCM
   completed the handshake with the Python connector. It took 7 to 15 ms on loopback over three runs.
3. **The real API works through the tunnel.** The page asked for `GET /api/projects` and got the registered projects
   back with status 200, through the relay, from a real daemon.
4. **The relay saw only ciphertext.** In the final run: 831 frames, 33 MB, and none of the markers searched for
   (`alpha`, `beta`, `x-cuttlefish-token`, `/api/projects`, `/api/auth-mode`, `hello`). An earlier run also searched
   for the three bytes `GET` and hit once, which is chance: about 0.4 matches are expected in 7 MB of random bytes.
5. **A browser key can be unreadable to script.** The static key is generated with `extractable: false`, stored in
   IndexedDB and reused after a reload (same public key). `exportKey` on it throws for both raw and PKCS8. Script on
   the page can use the key but not copy it out. It does not stop script from using it while the page is open.
6. **A tampered frame is rejected** and the same frame untampered is accepted.
7. **Speed is not the constraint.** Small calls: median 2.6 to 7 ms, 95th percentile 4 to 8 ms (loopback, so no
   network). A 5 MiB body: 7 to 12 MiB/s with JSON and base64 frames. A 5 and a 20 MiB body: 15 to 20 MiB/s with binary
   frames and the AES key imported once. A phone's connection will be the limit, not encryption.

## Findings that change the design

- **A Noise message is at most 65535 bytes.** A larger body needs chunking in our framing. The prototype used 60000
  byte chunks, which worked. This is the reason the ADR defines streams.
- **The daemon cannot trust a client at the end of the handshake.** With a wrong psk the browser rejected the
  daemon's reply, but the daemon had already logged "handshake done" and held the client's static key. Noise gives
  the responder no proof until the initiator's first transport message. The daemon must register a device only after
  it decrypts a valid first message.
- **Whoever sees the pairing code can pair.** The psk and the daemon's public key travel in the QR code, so a
  photographed QR would pair a stranger. The ADR adds a single use, a short expiry and a confirmation on the PC.
- **The dashboard has few network call sites.** `frontend/src/lib/api.ts` has four `fetch` calls (auth mode, login, the
  client's request method and one blob fetch) and `TerminalView.svelte` has the only `WebSocket`. Putting a transport
  under them is a small change, not a rewrite.

## Browser support

- X25519 in WebCrypto: Chrome 133 and later, Firefox 130 and later. For Safari the sources disagree.
  [Igalia's timeline](https://blogs.igalia.com/jfernandez/?p=1913) puts it in Safari Technology Preview in January
  2025, while [caniuse](https://caniuse.com/mdn-api_subtlecrypto_derivebits_x25519) lists Safari 17 and later. I could
  only test Chromium. The client should feature-detect and fall back to a JavaScript implementation (for example
  `@noble/curves`), so Safari's date does not decide anything.
- Web Push on iOS works only for a web app saved to the Home Screen (iOS 16.4 and later), after a tap on the app's
  own subscribe button. The Badging API arrives with it
  ([WebKit](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados)). Not tested here.

## Not checked

- Safari and Firefox behaviour of the prototype. Only Chromium 153 was run.
- Real internet latency, a mobile network, and many concurrent devices or rooms.
- Web Push through a real push service, and a service worker reading the device key.
- WebAuthn step-up on a phone.
- A passkey or other account login for the relay. The prototype has no accounts.
- Whether `ffmpeg` compression of video is fast enough on a modest PC.
