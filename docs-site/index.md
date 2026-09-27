---
icon: lucide/rocket
---

# cuttlefish-crew

A fleet manager for teams of coding sub-agents, running across many
software projects at once — so you stop babysitting a single agent's
context window, and stop losing track of what a dozen background tasks
actually did.

Register a project, name a small team of roles (a builder, a reviewer, an
ops-checker — whatever shape fits), and give each one a task in plain
language. Every role hands its actual coding work to a pluggable backend —
[kopicode](https://github.com/leejianrong/kopicode), headless Claude Code,
or headless Codex — rather than doing the edit itself, and if the process
dies partway through, restarting it resumes from where it left off instead
of starting over. The core loop is a durable
[satay](https://github.com/leejianrong/satay-runtime) workflow from the
first commit that runs a task, not an ordinary function made durable
later. Everything that happens is written to one readable record — no
scattered logs that disagree with each other.

!!! note "Where this fits"
    cuttlefish-crew is a **crew**, not a **company**: a lighter mental model
    than an org-chart/hiring-workflow tool. No SSO, no performance reviews,
    no departments — a handful of named agents per project, directed from
    one dashboard.

## Install

```bash
git clone https://github.com/leejianrong/cuttlefish-crew.git
cd cuttlefish-crew
uv sync
```

You'll also need at least one coding-agent backend on `PATH` —
[kopicode](https://github.com/leejianrong/kopicode) (the default), the
`claude` CLI, or the `codex` CLI — plus a model credential for whichever
backend and reasoning provider you select (see
[Configuration](cli-reference.md#configuration)). `cuttlefish` checks for
the binary before accepting a task, not mid-run.

## Try it with no credential at all

```bash
CUTTLEFISH_LLM_PROVIDER=replay uv run cuttlefish run "add a .gitignore entry for build artifacts"
uv run cuttlefish show <task-id>   # printed by `run`, above
```

`CUTTLEFISH_LLM_PROVIDER=replay` swaps in a keyless, deterministic provider
for cuttlefish's own reasoning calls (handover summaries) — useful for
smoke-testing the CLI itself. The actual coding work still goes through
your configured backend and its own credential.

Next: [Quickstart](quickstart.md) walks through registering a real project
and running a small team from the dashboard.
