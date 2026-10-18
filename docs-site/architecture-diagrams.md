---
icon: lucide/layout-dashboard
---

# Architecture in C4

Five views of cuttlefish-crew, from the outside in, following the [C4 model](https://c4model.com): context,
containers, two component views of the fleet daemon, and one deployment. They are drawn from one model file,
so a box means the same thing wherever it appears. Click a diagram for full size; they follow your light or dark
setting.

These describe the code as it is. Where one disagrees with the code, the code is right: fix the model in the same
PR as the change. The reasons behind each shape are in [`docs/adr/`](https://github.com/leejianrong/cuttlefish-crew/tree/main/docs/adr).

| View | Question it answers |
|---|---|
| [1. System context](#1-system-context) | Who and what does cuttlefish-crew talk to? |
| [2. Containers](#2-containers) | Which processes and stores make it up? |
| [3a. Fleet daemon: running a team](#3a-fleet-daemon-running-a-team) | How does a task reach a coding agent and get journalled? |
| [3b. Fleet daemon: access, devices and sessions](#3b-fleet-daemon-access-devices-and-sessions) | How does a browser or phone reach the daemon, and who decides what it may do? |
| [4. Deployment: Tailscale https](#4-deployment-tailscale-https) | What runs where when a phone pairs over Tailscale? |

## 1. System context

The operator manages a fleet of coding agents from a dashboard on the PC or a paired phone.
cuttlefish-crew does not write code itself: it delegates to a coding agent's own command-line tool
and keeps the record.

[![System context: the operator, cuttlefish-crew and what it talks to](assets/architecture/c4-1-context.svg)](assets/architecture/c4-1-context.svg)

*Click for full size.*

Not shown: [satay-runtime](https://github.com/leejianrong/satay-runtime), which is a library inside
the daemon rather than a system of its own, and the model providers the coding agents call
themselves, which cuttlefish-crew never sees.

## 2. Containers

One daemon process runs every project's team. The relay is a separate, headless process and holds
no project data. Each project keeps its own journal and satay store in its own folder.

[![Containers: dashboard, fleet daemon, relay, CLI, MCP server, agent sessions and the SQLite stores](assets/architecture/c4-2-containers.svg)](assets/architecture/c4-2-containers.svg)

*Click for full size.*

The relay and the dashboard's relay code are meant to be lifted into their own repository, so
`cuttlefish.relay.protocol` and `frontend/src/lib/relay/` import nothing else from cuttlefish
(ADR-0037, enforced by a test).

## 3a. Fleet daemon: running a team

A task is a satay workflow from its first line. A team runs its roles concurrently and every
effect goes through a task, so a restart resumes without repeating the finished ones.

[![Components of the fleet daemon that run a team](assets/architecture/c4-3a-team.svg)](assets/architecture/c4-3a-team.svg)

*Click for full size.*

Two rules this view carries: a project secret's decrypted value is never a satay task argument or
return value (ADR-0006), and a satay control call made by the daemon itself goes through
`asyncio.to_thread` because they share one event loop (ADR-0009).

## 3b. Fleet daemon: access, devices and sessions

Every request passes one of three guards. The owner's own dashboard is not limited by tier; a
paired device is, per route.

[![Components of the fleet daemon for access, devices and sessions](assets/architecture/c4-3b-access.svg)](assets/architecture/c4-3b-access.svg)

*Click for full size.*

A device is trusted only after its first transport message decrypts, and a device the owner has not
confirmed on the PC is served nothing (ADR-0037, ADR-0039). A held hook is released at shutdown, so
uvicorn is not left waiting on a person (ADR-0040).

## 4. Deployment: Tailscale https

What runs where when `make phone HTTPS=1` or `cuttlefish serve --tailscale-https` is in use
(ADR-0041). The daemon and the relay listen on loopback only. `tailscale serve` ends TLS and
forwards, so nothing in cuttlefish holds a certificate.

[![Deployment: a phone pairing over Tailscale https](assets/architecture/c4-4-deployment.svg)](assets/architecture/c4-4-deployment.svg)

*Click for full size.*

Plain `cuttlefish serve --tailscale` is simpler: the daemon binds the tailnet address over plain http
(ADR-0013) and a phone can sign in with the password but cannot pair, because browsers withhold
WebCrypto from a plain-http page.

## Keeping these true

The model is [`docs/architecture/model.mjs`](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/architecture/model.mjs).
Edit it, then run `make diagrams` to redraw the SVGs in `docs-site/assets/architecture/` and commit both. Every box
names the module that implements it, so a rename shows up in review: search the model for the old path when you move one.

- A new process, store or outside system changes the context or container view.
- A new guard, route family or backend changes a component view.
- A change to what runs where changes the deployment view.

The drawing is automatic (ELK, layered, orthogonal edges), so a crossing line is fixed by reordering nodes or edges in
the model, not by dragging anything. `docs/architecture/README.md` has the details.
