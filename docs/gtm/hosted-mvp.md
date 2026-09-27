# Hosted-service MVP on Fly.io (KAN-1719)

A product/pricing wrapper around ADR-0015's own feasibility spike: "sign up
and get a running fleet daemon" instead of self-host only. ADR-0015 already
answered the infrastructure question (one Fly app per tenant, a `Dockerfile`
and `fly.toml.example`, built and run locally, never against Fly's real
service). This card answers the product question on top of it: what does a
real person actually experience, and what does it cost.

**Scope boundary, stated up front, matching ADR-0015's own**: nothing in
this document gets provisioned, deployed, or billed by this session.
`flyctl` is already authenticated to Jian's own real account per CLAUDE.md's
own secrets/infra section - creating a real Fly app, attaching a real
volume, or setting a real secret against it is exactly the kind of action
that needs his sign-off first, not an autonomous default. This is a plan to
review, not a deploy to approve after the fact.

## What it actually costs to host one tenant

Verified against Fly's own current public pricing (`docs.fly.io/about/pricing`,
checked 2026-09-28), not estimated from memory:

- **Compute**: `fly.toml.example`'s own configured size, `shared-cpu-1x` /
  512MB, costs about **$3.19/month if always-on** (1 vCPU at
  $0.00000075/second, plus 0.25GB of RAM beyond the included 256MB at
  $0.00000193/GB/second, over a 30-day month). But the example config
  already sets `auto_stop_machines = "stop"` / `min_machines_running = 0` -
  scale-to-zero, a real cost lever this project already reasoned through,
  not a hypothetical one. A tenant who isn't actively running a team most of
  the day pays for a small fraction of that $3.19, not the whole month.
- **Storage**: $0.15/GB/month, pro-rated hourly. A tenant's own `HOME` (the
  project registry, secrets store) plus a couple of small project checkouts
  and their episodic/satay stores is realistically 2-5GB - **$0.30 to
  $0.75/month**.
- **Total, realistic**: **$1-4/month per tenant** for someone who isn't
  running teams continuously; **up to ~$4/month** if they set
  `min_machines_running = 1` for always-on responsiveness instead.

**What isn't verified**: real memory usage under a genuinely concurrent
multi-role team (several `kopicode`/`claude`/`codex` subprocesses at once)
hasn't been load-tested against 512MB - ADR-0015's own local Docker run
proved the stack boots and serves correctly, not that 512MB holds up under
real concurrent load. Budget for possibly needing `shared-cpu-1x`/1GB
(roughly $6-7/month always-on by the same math) once that's actually
observed, rather than assuming 512MB is enough.

## Pricing

Two tiers, not more - this is a first MVP with zero paying customers so far,
not a mature product with a proven segmentation story to price against:

- **Self-host: free, Apache-2.0.** The default, and genuinely the better
  option for the primary validated persona (`docs/gtm/personas.md` P1, a
  solo developer already comfortable running `cuttlefish serve` themselves).
  Nothing about the hosted tier below should read as "the self-host option
  is second-class" - it isn't.
- **Hosted (early access): $9/month, flat.** One Fly app, one operator, one
  password - no usage-based metering, no per-project limits, because this
  architecture has no shared-tenant infrastructure to meter in the first
  place (ADR-0015's own finding). Priced as "early access" on purpose: real
  infra cost is $1-4/month at the low end, so $9 leaves margin for the
  possibility that real usage needs the bigger machine, plus Jian's own time
  answering support requests as a solo maintainer - not a number to defend
  as final before a single real tenant has run on it.

## The actual "sign up" flow, and what it deliberately isn't yet

**What this MVP should ship first: a manual, concierge flow, not
self-service automation.** Nothing here has a single paying customer yet,
and building an automated signup-to-billing-to-provisioning pipeline before
that is exactly the kind of "build ahead of a proven need" this project has
already talked itself out of more than once (ADR-0002's own standing
position; Q54's interim-fix precedent). Concretely, for the first cohort
(the people who show up from KAN-1720's launch posts and actually want
"just run it for me"):

1. A "Hosted" link on the landing page (not added yet - deliberately, since
   there's no flow behind it to point to today) goes to a plain form or a
   `mailto:`, asking for an email and which region they want.
2. Jian runs `fly launch --no-deploy` from `fly.toml.example` by hand,
   renames the app, sets `CUTTLEFISH_SERVE_PASSWORD` via `fly secrets set`
   himself, and deploys it - the exact commands ADR-0015 and this repo's own
   `fly.toml.example` comments already describe, just run once per tenant
   instead of never.
3. He emails them the app's `https://<app>.fly.dev` URL and the password.
   Billing, for this first cohort, is a Stripe payment link or an invoice -
   not integrated into any signup flow at all.

**What real automation would need, once that's validated with real
tenants**: a small control-plane service (separate from cuttlefish itself)
that takes a signup, creates a Stripe subscription, calls Fly's Machines API
to provision a new app from this same `Dockerfile`, generates and sets that
tenant's `CUTTLEFISH_SERVE_PASSWORD`, waits for a healthy deploy, and emails
the result - genuinely new engineering, its own future card once the manual
flow above has actually proven a handful of people want this enough to pay
for it. Not scoped or built here.

## What this explicitly does not claim

The landing page (KAN-1717) makes no "hosted" claim today, and shouldn't
until the flow above exists for real - the same discipline
`docs/gtm/personas.md`/`positioning.md` already applied to "remote access
from anywhere." A hosted tier that's one `mailto:` and a manual `fly deploy`
away from existing is real progress on this card's own question (what would
this cost, what would it feel like), not a shipped product.
