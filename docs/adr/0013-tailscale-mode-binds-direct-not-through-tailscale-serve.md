# ADR-0013: `cuttlefish serve --tailscale` binds directly to the tailnet interface (ADR-0011's own non-loopback mode); `tailscale serve`'s own reverse proxy is not recommended in front of it

- Status: Accepted
- Date: 2026-09-27
- Deciders: Jian

## Context

KAN-1708 (CUT-E9, third card): "ship the same recommended pattern Paperclip
documents for its authenticated+private mode: works over Tailscale/VPN
without requiring a public-internet exposure or a hosted offering first."
ADR-0011 (KAN-1706) already built the load-bearing mechanism this needs — a
non-loopback bind with real password/session auth — and ADR-0012 (KAN-1707)
already made the daemon serve its own dashboard, same origin. What's actually
missing is the specific, *recommended* way to combine them for Tailscale.

Two shapes exist, and they are not equivalent:

**Shape A: bind `cuttlefish serve` directly to this machine's own tailnet
address** (`100.x.y.z`, via ADR-0011's non-loopback mode) — Tailscale's own
WireGuard mesh is the only thing between the operator's phone/laptop and the
daemon; no separate proxy in the path.

**Shape B: keep `cuttlefish serve` loopback-bound and front it with
`tailscale serve`**, Tailscale's own built-in reverse proxy, which also
provisions a real TLS certificate for the tailnet's own MagicDNS name — the
shape that gets an operator a padlock-verified `https://` URL for free.

Shape B looks strictly better (real TLS, zero cuttlefish-side auth-mode
change needed) until checked against how `tailscale serve` actually proxies a
request. Checked directly against Tailscale's own source
(`tailscale/tailscale`, `ipn/ipnlocal/serve.go`, `main` branch, function
`reverseProxy.ServeHTTP`'s `httputil.ReverseProxy{Rewrite: ...}`):

```go
// For Unix sockets, use the URL's host (localhost) instead of the incoming host
if rp.socketPath != "" {
    r.Out.Host = rp.url.Host
} else {
    r.Out.Host = r.In.Host
}
```

For a network-address backend target (`127.0.0.1:<port>`, the only kind
`tailscale serve` supports as a reverse-proxy target at all — its own docs:
"only `http://127.0.0.1` is supported for proxies") — the `else` branch is
what fires. **`tailscale serve` forwards the original inbound `Host` header
(the tailnet's own MagicDNS FQDN) verbatim to the backend; it does not
rewrite it to the loopback target.** A loopback-bound `cuttlefish serve`
reuses `satay.control.SecurityPolicy` (ADR-0009), whose own `Host` check
(`is_loopback_host`, ADR-0014) rejects exactly this — every request proxied
through `tailscale serve` would get a 403 "disallowed Host", even though the
underlying TCP connection reaching the backend is a legitimate loopback hop.
This is real and sourced, not a guess: verified against Tailscale's actual
Go source, not assumed from its (silent-on-this-point) public docs.

Fixing this would mean either asking satay-runtime to special-case a local
reverse-proxy's `Host` passthrough (a real change to satay's own control API
purely to accommodate one specific proxy's behavior — well outside "a
concrete, narrowly scoped ask," `docs/SLICES.md`'s Out list), or having
cuttlefish's own loopback-mode security layer stop trusting `Host` at all and
instead check the actual TCP peer address — a real, separate redesign of the
loopback guard itself, not what this card asks for. Neither is this card's
job.

Shape A sidesteps the whole problem instead of fixing it: `SessionAuth`
(ADR-0011) never inspects `Host` at all — it was already built for a bind
that isn't loopback, so there's no rewritten-vs-passthrough question to have.

## Decision

**`cuttlefish serve --tailscale` binds directly to this machine's own tailnet
IPv4 address, resolved via `tailscale ip -4` (`cuttlefish._resolve_tailscale_host`),
using ADR-0011's existing non-loopback/password mode — not through
`tailscale serve`'s own reverse proxy.** `--tailscale` overrides `--host`
outright (documented, not an error) and still requires
`CUTTLEFISH_SERVE_PASSWORD`, since it is, definitionally, a non-loopback
bind. `_resolve_tailscale_host` raises a clear, actionable `RuntimeError` for
every real failure mode (`tailscale` missing from `PATH`, `tailscaled` not
running, this machine not logged into a tailnet) rather than surfacing a bare
`CalledProcessError` or a generic bind failure.

**`SessionAuth`'s `allowed_origins` always includes the daemon's own bind
origin** (`cuttlefish.fleet.server._self_origin`, `http://{host}:{port}`), in
addition to whatever `--allow-origin` the operator adds. This is safe for the
same reason the pre-existing loopback-origin carve-out already is: DNS
rebinding forges which *address* a request reaches, never the browser's own
`Origin` header (derived from the page's actual navigated domain, the
attacker's, not the rebound target) — a request whose `Origin` exactly
matches this daemon's own bind can only come from a browser that was
actually pointed at it on purpose. This closes the loop KAN-1708 asks for
end-to-end: `cuttlefish serve --tailscale --dashboard-dir frontend/dist`,
then just open the URL it prints on the tailnet, no `--allow-origin` flag
needed for the common case.

**No TLS in this mode, and that is an accepted, reasoned gap, not an
oversight.** Tailscale's own WireGuard mesh already encrypts every packet
between tailnet peers end-to-end (Tailscale's own security docs:
`tailscale.com/security`, `tailscale.com/docs/concepts/tailscale-encryption`)
— the plain-`http://` URL a browser sees is a UI/certificate-padlock
limitation, not an unencrypted wire. An operator who wants a real `https://`
URL too still can layer `tailscale serve` in front, understanding (from this
ADR) that doing so requires either accepting the `Host`-passthrough
incompatibility with loopback mode (a real, cited limitation, not resolved
here) or pointing `tailscale serve` at nothing at all and using Shape A's own
plain-HTTP tailnet address directly instead, which is this ADR's own
recommendation.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Recommend `tailscale serve` fronting a loopback-bound `cuttlefish serve` (Shape B) | Rejected on sourced evidence above — `tailscale serve`'s own Host-passthrough behavior breaks satay's own loopback `Host` check for every proxied request. Not a hypothetical: read directly from Tailscale's current `main`-branch source. |
| Ask satay-runtime to relax or special-case its own `Host` check to tolerate a trusted local proxy | Out of bounds per the standing "no changes to satay-runtime beyond a concrete, narrowly scoped ask" rule — a change to satay's own control-API security posture, motivated by one specific third-party proxy's behavior, is not that. |
| Skip the self-origin-trust default and just tell operators to always pass `--allow-origin` matching their own tailnet address | Real friction for zero security gain — the address is the same one the CLI just resolved and printed; asking the operator to retype it into a second flag is exactly the kind of manual step KAN-1708's own "cheapest real path" framing asks to remove. |

## Consequences

`cuttlefish serve --tailscale` is now a real, complete, one-flag remote-access
path: Tailscale's mesh gates *who* can reach the address at all, ADR-0011's
password/session layer gates *what they can do* once they're on it, and
ADR-0012's same-origin dashboard means there's exactly one URL to open. The
named gap (no TLS, no `tailscale serve` compatibility) is honest and
deliberate, not silently dropped — `docs/QUESTIONS.md`/`CLAUDE.md` records it
alongside ADR-0011's own non-loopback-mode gaps rather than re-litigating
them. None of this has been verified against a real, live tailnet in this
environment (no `tailscale` binary or account available here) — the
Host-header finding is sourced directly from Tailscale's own code, and
`_resolve_tailscale_host`/`_self_origin` are unit-tested against mocked
`subprocess` calls and plain string logic respectively, but the full,
real-world "open it on my phone" path remains unverified live, named here
rather than glossed over.
