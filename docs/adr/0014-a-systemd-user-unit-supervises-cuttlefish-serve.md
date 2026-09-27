# ADR-0014: A systemd `--user` unit supervises `cuttlefish serve`; Docker/container restart policies are deferred to KAN-1710

- Status: Accepted
- Date: 2026-09-27
- Deciders: Jian

## Context

KAN-1709 (CUT-E9, fourth card): "a real supervisor (systemd unit / docker
restart policy / equivalent) so `cuttlefish serve` dying doesn't require a
human to notice and restart it manually." Named `blocked_by` KAN-1703 in the
board's own dependency graph — correctly: a supervisor that blindly restarts
a dead daemon is only *safe* once a restart actually resumes every project's
in-flight team rather than silently abandoning it. KAN-1703 (ADR-0010,
`FleetDaemon.resume_pending`) shipped that already; this card is purely the
"restart it automatically" half, composed on top, not a second continuity fix.

Two real supervisor shapes exist, and this project's own persona work
(`project_paperclip_positioning`: "primary, validated: solo developer running
10-100 coding-agent sessions... Jian himself") points at one of them:

- **A systemd unit**, run directly on the operator's own machine (or a bare
  Linux VM) — no container runtime required, `systemctl --user` needs no
  `sudo` at all, and this repo's own dev environment already runs systemd as
  PID 1 (verified: `ps -p 1 -o comm=` prints `systemd`), so this is directly
  testable here, not just written.
- **A container restart policy** (`docker run --restart unless-stopped`, a
  Compose file, or a platform's own equivalent) — the natural shape for
  KAN-1710's own Fly.io feasibility spike, which needs a `Dockerfile`/image
  for a completely different reason (Fly.io deploys containers, not bare
  processes). Building that image now, for this card, would either duplicate
  KAN-1710's own work or lock in packaging choices before that spike has
  actually run.

## Decision

**Ship a systemd `--user` unit** (`deploy/systemd/cuttlefish-serve.service.template`,
installed via `scripts/install-systemd-service.sh`/`make install-systemd-service`)
as the supervised-restart path for this card. `--user`, not a system-wide
unit: it needs no `sudo`, matches the solo-operator persona directly, and
avoids the sharper edges of a root-owned service (file permissions, running
as a dedicated system account) that a personal machine has no real need for.
`Restart=on-failure` + `RestartSec=2` is the actual supervision; the
template's own comments point at where to add `--tailscale`/`--dashboard-dir`
flags or an `EnvironmentFile=` for `CUTTLEFISH_SERVE_PASSWORD` (ADR-0011),
rather than the install script trying to parameterize every combination.

**`systemctl --user` needs `loginctl enable-linger $USER` to survive the
operator logging out** — without it, a `--user` unit's whole scope
(including this one) stops the moment the last login session for that user
ends, which would silently defeat "auto-restart without needing a human to
notice," the exact thing this card exists to fix. `install-systemd-service.sh`
checks `loginctl show-user ... -p Linger` and prints a clear note if it isn't
enabled, rather than leaving this as a footnote an operator finds out the
hard way after their first reboot.

**Live-verified in this sandbox** (not just written and reasoned about),
using the actual shipped `scripts/install-systemd-service.sh` against this
real repo, then temporarily pointed at a `HOME`-isolated demo project (never
the real `~/.cuttlefish/projects.db`) and a throwaway port for the test
itself: started under `systemctl --user`, a project registered against it,
its `cuttlefish serve` process (not the unit wrapper) `kill -9`'d directly,
and `systemctl --user status` confirmed a fresh `MainPID` within seconds —
`Restart=on-failure` genuinely fires, not just on paper — printing a new
startup line with a fresh token, and the registered project's state (read
straight back over the API) was intact on the restarted process. This
exercised a *live crash → live restart → live day of the daemon coming back
up correctly*, but not a genuinely in-flight team specifically (no real
delegation was started, to avoid needing live agent-backend credentials in
this environment) — that composition (a restart resuming a team that was
still non-terminal, not merely a registry surviving) is exactly
`FleetDaemon.resume_pending`'s own already-tested scope
(`tests/integration/test_fleet_resume.py`), not re-derived here. This ADR's
own live test targets the genuinely new thing KAN-1709 adds — the supervisor
actually restarting the process at all — not re-proving KAN-1703's resume
correctness a second time. This template's own bug (a cosmetic doubled `//`
in a generated comment, from concatenating two already-absolute-path
placeholders) was caught by this same live run and fixed before merge. The
throwaway unit and its test edits (isolated `HOME`, throwaway port, a log
file to read the printed token back since this environment's journald
persists no history) were fully removed afterward — stopped, disabled, unit
file deleted, `daemon-reload`'d — confirmed `not-found` again; nothing was
left installed or running.

**Docker/container restart policies are named, deferred to KAN-1710, not
built here.** KAN-1710's own Fly.io feasibility spike needs a `Dockerfile`
for its own, separate reason; building one now for supervision alone risks
locking in image/packaging choices that spike hasn't made yet. If KAN-1710
lands a `Dockerfile`, a restart policy on top of it (`restart:
unless-stopped` in Compose, or the platform's own equivalent) is close to
free — this ADR does not need to re-litigate that when it happens, only to
say honestly that it hasn't happened yet.

## Alternatives considered

| Option | Why not |
|--------|---------|
| A system-wide (root-owned) systemd unit | Real, but needs `sudo` to install and a dedicated service account to run safely (never run an operator-facing daemon as root) — meaningfully more setup for a personal machine that has no second user to isolate from. `--user` fits the actual persona better. |
| Build a `Dockerfile` + Compose restart policy now, for this card | Would duplicate or preempt KAN-1710's own image/packaging decisions, which that spike hasn't made yet — this ADR's own "no changes to satay-runtime beyond a narrow ask" sibling discipline applies here too: don't build ahead of a decision another card is explicitly scoped to make. |
| A generic process-supervisor tool (supervisord, pm2, runit) instead of systemd | Adds a new runtime dependency for something the operator's own OS (any real Linux desktop or server) already ships — systemd is already there on the overwhelming majority of target machines; a third-party supervisor earns its keep only on a machine that genuinely lacks systemd, which isn't this persona's common case. |

## Consequences

`cuttlefish serve` dying (a crash, an OOM kill, a laptop sleeping and the
process getting reaped) is no longer something the operator has to notice
and fix by hand for the systemd path — verified live, not just designed.
ADR-0009's own Consequences section ("a daemon restart loses every in-memory
running-team handle... there is no separate process supervising `cuttlefish
serve` itself yet") is now closed for an operator who installs this unit;
an operator who doesn't is exactly where ADR-0009 already left them, unchanged.
The container-restart-policy half of KAN-1709's own description remains
real, named future work, picked up naturally once KAN-1710's own `Dockerfile`
exists rather than duplicated ahead of it.
