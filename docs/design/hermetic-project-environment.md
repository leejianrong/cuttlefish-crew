# Design note: a hermetic environment per project

Status: a design note, not a decision. No code, no ADR. V5-E6c. It says what the sandbox seam
already gives us, what a per-project container would add that V5-E3 to E6a do not, and what we would
need to find out before building it. Read it with ADR-0002 (the sandbox stays internal) and
ADR-0029 (the environment is cuttlefish's job).

## The problem V5 left over

V5 makes a project's environment on the host work: detect it, install into it, put it first on
`PATH`, tell the agent. That fixes "the agent used the wrong Python". It cannot fix what the host
itself does not have:

- A project that needs Python 3.11 on a host with 3.12, or Node 20 on a host with 24. We detect
  the version hint and report it; we cannot honour it. `uv` can fetch a Python, nothing of ours
  fetches a Node.
- System libraries and services: `libpq`, `ffmpeg`, a database on a port, Docker itself. No
  per-project install step can supply them.
- Installs that write outside the project (`~/.m2`, the gem home, the Go module cache) and leave
  state that one project's install can disturb for another's.
- An agent that runs with the operator's whole home directory, credentials and network. The
  allowlisted environment (V5-E4) withholds variables; it does not stop a command reading
  `~/.ssh` or `~/.aws`.
- "Works on my machine": two operators, or the same operator a month later, get different
  results from the same repository.

A container per project, built from what `EnvironmentSpec` already says, addresses all of these
with one mechanism. It is also the largest change on the list, which is why it is a note.

## What already exists

- **The seam** (`cuttlefish.sandbox.provider`): `create(spec)`, `exec`, `snapshot`, `destroy`, and an
  opt-in `spawn` that keeps stdin and stdout open. `SandboxSpec` has an image (`template`),
  `envs`, `mounts` and a timeout. `ContainerSandboxProvider` runs `docker run -d ... sleep`, as the
  operator's own uid and gid, with the host's CA bundle mounted read-only. E2B is the other backend
  (no mounts, no `spawn`).
- **A kopicode inside a sandbox** works today: `run_kopicode_serve(process_factory=...)` starts
  `kopicode serve` through `spawn`, and the delegation task already routes through
  `runtime.sandbox_provider`. Nothing selects it per project; it is one provider for the whole
  daemon.
- **What the daemon knows about a project**: `detect(root)` (ecosystems, tools, version hints),
  `envprep.plan` (the commands), and `.cuttlefish/env.json` (fingerprints of what was installed).

So the pieces for "run the same commands in a container" are mostly there. What is missing is an
image, a lifecycle, and a decision about where state lives.

## What a per-project container would be

One long-lived container per project, started when a team starts and kept for the team's life,
with the project root bind-mounted at the same path inside and outside (the path matters: kopicode's
`--root`, its session record and the stuck-agent detector all use it).

1. **Image from the spec, not from a Dockerfile the operator writes.** A small set of base images
   (`python:<minor>`, `node:<major>`, `golang:<minor>`, `rust:<version>`, `eclipse-temurin:<major>`,
   `ruby:<minor>`) chosen from the version hints, with a layer for the tools cuttlefish needs
   (`git`, `uv`, the agent binary). A project that already has a `.devcontainer/devcontainer.json`
   or a `Dockerfile` can name its own image; supporting devcontainer features in full is out of scope
   and would be a product of its own.
2. **Installs run inside it**, with `provider.exec`, through the same `envprep.run_step` argv lists.
   The only change is where the process starts.
3. **The agent runs inside it**, through `spawn` (kopicode) or `exec` (Claude Code, Codex: their CLIs
   would need to be in the image or mounted in, which is the least-certain part, see questions).
4. **The record stays visible.** With the root bind-mounted at the same path, kopicode's
   `.kopicode/sessions/<id>/events.jsonl` is on the host too, so the stuck-agent detector (V5-E5) can
   watch a containerised agent. Today `run_kopicode_serve` skips the watch for any `process_factory`
   child; with an identical bind mount that restriction could be lifted.

## Decisions this forces

**Where the dependencies live.** A host `.venv` cannot be mounted into a container: its interpreter
path and shebangs are absolute and point at the host's Python. Options:

| Option | Cost |
|---|---|
| Install into the image at a fixed path (`/opt/venv`) and keep the project's `.venv` out of the way. | The image is rebuilt when the lockfile changes (the fingerprint we already compute). Cleanest, slowest first start. |
| Install into a named volume mounted over `node_modules` or `.venv`. | Fast rebuilds, but the volume is state cuttlefish now owns and must reap. |
| Install into the bind-mounted project folder from inside the container. | Simplest. The host then holds a `.venv` its own tools cannot run, so the operator cannot use it. |

The first is the one worth designing around: the image tag is the fingerprint, so "is it
installed" becomes "does this image exist", which is exactly the question V5-E6a could not answer for
Go, Rust, Java and Ruby.

**Credentials and network.** The allowlist (V5-E4) says what variables an agent sees. A container
adds what files and network it sees: no home directory, no `~/.ssh`, and an explicit decision about
outbound traffic. A default of full outbound access with nothing else mounted matches what the host
agent can reach today minus the files; "no network after the install" is a stricter mode worth
offering, since it is what makes a run hermetic in the sense of reproducible. Git over SSH, which
many projects need, would then need an explicit opt-in.

**Who owns the lifecycle.** The fleet daemon, per team: create on start, destroy on stop or end,
and sweep leaked containers on daemon start (the same idea as abandoning requests after a restart).
`docker run -d ... sleep infinity` plus a label per project and team makes the sweep one command.

**Docker is a dependency.** On WSL it means Docker Desktop's integration or a daemon in the
distribution; on the operator's machine it may not exist. This has to be opt-in per project (a
setting beside `env_prepare`), with `cuttlefish doctor` saying whether Docker is usable, and
host mode staying the default and fully supported.

**Boundary.** A container narrows what an agent can do; it is not the never-allowed list, which must
keep holding in every mode (V4). The command gate stays in front of the container, not replaced by it.

## What this would not fix

- Agents still produce and run code the operator has not read. A container limits the blast
  radius; it does not make the code safe, and ADR-0002's trigger conditions are about exposure, not
  about this note.
- Services a project's tests need (a database) are a Compose problem. A per-project container
  makes it possible to start them beside the agent; designing that is a second step.
- macOS and Windows bind mounts are slow and have their own uid behaviour. This note was written
  from Linux and WSL experience; those hosts are untested.

## Open questions, in the order they would block work

1. **Do the Claude Code and Codex CLIs run in a stock image?** The container provider's own gaps
   (`agent_docs/known-gaps.md`)
   say not entirely: Claude Code's sandboxed path works only with an `ANTHROPIC_API_KEY`, not its OAuth login, and Codex's fails
   closed on a 401 (verified live, ADR-0018). This decides whether containers are kopicode-only at first.
2. **How slow is image build plus install on a typical project**, against a host install, which we
   have not timed either? If the first start costs minutes, it must be visible as its own step
   (`EnvironmentPrepareStarted` already has the shape) and cacheable across projects with the same
   lockfile.
3. **Does a bind mount preserve what we rely on**: file ownership (the container provider already
   runs as the operator's uid), `git` operating on the mounted repository (safe-directory prompts),
   and kopicode's lock file under `.kopicode/`?
4. **One container or one per role.** Two roles on one project share a root today and, for kopicode,
   serialise on its lock. A shared container keeps that; per-role containers would not make two
   kopicode agents safe on one tree.
5. **Does it justify spinning the sandbox out** (ADR-0002)? Only a second real consumer would. Per-project
   containers are still this product's own use of the seam, so the answer so far is no.

## Suggested slices if it is ever built

1. `doctor` reports Docker availability, the image a project would use, and whether it exists locally.
   No behaviour change; it answers question 2 with data.
2. A project setting `environment: host | container`, default `host`, and install-in-container for
   Python and Node only, behind the existing `env_prepare` confirmation.
3. Run kopicode in it (re-enabling the stuck-agent watch through the mount), then the other backends
   as question 1 allows.
4. Network policy, then Compose services.

Each slice is useful alone and none changes the default, so none of this blocks V5.
