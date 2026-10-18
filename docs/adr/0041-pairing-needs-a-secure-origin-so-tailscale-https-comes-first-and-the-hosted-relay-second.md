# ADR-0041: Pairing needs a secure origin, so Tailscale https comes first and the hosted relay second

- Status: Accepted
- Date: 2026-10-18
- Deciders: Jian

## Context

Pairing and the relay use WebCrypto (`crypto.subtle`), which browsers withhold from any page that is not a secure context:
https, or http on localhost. A page at `http://<tailscale ip>:<port>` is not one (measured in Chromium and Firefox on a real
Tailscale address; ADR-0039 records the finding). So a phone cannot pair from the plain-http Tailscale dashboard, and the
same missing secure origin blocks service workers, Web Push, an app badge and installing the dashboard as an app (ADR-0037).
CUT-128 asked which way to get an https page. Three routes were set out: Tailscale https, the hosted relay serving the page
(CUT-121), and a pure-JavaScript crypto fallback that works on any origin.

## Decision

1. **Tailscale https comes first, for people who run cuttlefish themselves.** The daemon's page and the relay are each put
   behind `tailscale serve`, which gives them `https://<machine>.<tailnet>.ts.net` and `wss://` addresses with a real
   certificate. The pairing link then carries an https page and a `wss` relay address (an https page may not open a plain
   `ws://`). The relay stays: it reuses the protocol, pairing, device store, tiers and revocation as built, and the phone
   still needs the Tailscale app on the same tailnet.
2. **The hosted relay is the route for everyone else** (CUT-121, gated by the outside review of ADR-0037). It serves the https
   page itself and needs no VPN. It is the same code as route 1 with a different place for the page and the relay to live. A
   technical person may also run the relay on their own server behind their own TLS (Caddy, a Cloudflare tunnel); that is
   the same code again and needs nothing from us.
3. **The pure-JavaScript crypto fallback is dropped.** It would work on plain http but would not unlock push, install-as-app
   or a service worker (all need a secure origin), and it would weaken the argument that a hosted page's code arrived over
   TLS. It can come back later as a fallback if a need appears.
4. **Revisit, not decided:** whether a self-hoster should run a relay at all. Once the page is https, the daemon could be
   its own relay endpoint over `wss`, with no second process and no second `tailscale serve` port. That is simpler for the
   user and a bigger design change, so it waits until route 1 has been used.

## What Tailscale https needs, and who does it

- **Turn on HTTPS in the Tailscale admin console** (one toggle for the tailnet). Jian will do this when ready. Until then
  `tailscale status --json` shows no `CertDomains` (checked 2026-10-18) and `tailscale serve` has nothing to serve over https.
- It publishes the machine's name, and so the tailnet's name, in public certificate transparency logs. Say so wherever the
  feature is described.
- **`tailscale serve` changes the serve configuration of the machine's own Tailscale node.** Jian agreed it can be run once
  HTTPS is on, and asked to be asked first each time it is: it is not run by an agent without that.
- On the daemon's side: accept requests that arrive through `tailscale serve` (ADR-0013 records that serve forwards the
  tailnet `Host` header, which a loopback-bound daemon rejects), serve the relay's websocket as `wss`, and print a pairing link
  with the right addresses. A helper such as `cuttlefish serve --tailscale-https` is likely, as `--tailscale` is today.
- A plain-http Tailscale address keeps working for the password sign-in; only pairing and the relay need https.

## Consequences

- Self-hosters get pairing, tiers, push and install-as-app without any third party and before the hosted service exists, and
  the real-phone pass can be done over a secure origin.
- Nothing built for the hosted relay is wasted, and nothing built here is thrown away when it arrives.
- Two kinds of address now exist (the direct password path and the paired path); the dashboard already says which pages can
  pair and which cannot (ADR-0039).

## Alternatives considered

| Option | Why not |
|---|---|
| The pure-JavaScript fallback first | Quickest to build, but it leaves push and install-as-app without a secure origin, and gives up the TLS argument for the hosted page. |
| The hosted relay only | The right route for most people and the slowest: it needs accounts, hosting, abuse handling and the outside review before anything ships, and a self-hoster would wait for all of it. |
| Daemon as its own relay now | Simpler for a self-hoster, but a larger change before the first one has been tried. Kept as the thing to revisit. |
| A self-signed certificate | The phone would have to trust it by hand, which is the friction this is meant to remove, and some browsers refuse to run WebCrypto features on it. |

## Open

- The exact `tailscale serve` commands and ports, and whether the daemon or `tailscale serve` terminates TLS (`tailscale cert`
  with the daemon serving https itself avoids the Host-header problem and the serve config change, at the cost of renewal),
  are for the build to settle against a tailnet with HTTPS on. Not tested yet.
