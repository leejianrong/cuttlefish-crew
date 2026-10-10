---
icon: lucide/play
---

# Quickstart: register a project and run a crew

This walks through the path most people actually want: a dashboard showing
one or more registered projects, each running its own small crew of coding
agents.

## 1. Bring up the daemon and dashboard together

```bash
make demo
```

This builds the dashboard once, then starts `cuttlefish serve` — one
process serving both the JSON API and the dashboard itself, same origin.
It prints a URL and a token; open the URL and paste in the token. Ctrl-C stops
it cleanly. The daemon's log goes to this terminal and always to
`~/.cuttlefish/logs/cuttlefish.log` (rotating; `CUTTLEFISH_LOG_LEVEL=DEBUG` adds
every tool call). Each line names its project, team and role, and a failed team
start says why there. `make demo LOG=1` also copies the output to
`/tmp/cuttlefish.log` (or `LOG=<path>` for another file).

No daemon at hand yet and just want to look around? The dashboard's own
connect screen links to a sprite gallery that needs no connection at all.

## 2. Register a project

From the dashboard's portfolio view, use **Register project** — name, the
checkout it should delegate against, its roles, and (optionally) which
shell commands its team may run and a token/cost ceiling. This never
requires a terminal.

The same thing from the CLI, if you'd rather script it:

```bash
uv run cuttlefish projects add \
  --name demo \
  --root /path/to/your/checkout \
  --role builder:"careful, incremental, writes tests first" \
  --role reviewer:"terse, skeptical, flags anything undertested" \
  --allow "go test" \
  --allow "npm test"
```

`--role NAME:PERSONA` is optional per role — the persona is a persistent
voice/tone the role keeps across every task it runs, not just this one.

## 3. Start the crew

Click **Start** on the project card, or:

```bash
uv run cuttlefish run-team \
  --role builder:"implement the login form" \
  --role reviewer:"review the last commit for style issues"
```

Both roles start together (`satay.gather`) against the project's shared
checkout; two kopicode-backed roles are dispatched one at a time instead,
because kopicode locks a working tree per session. Watch progress live in the dashboard — each role renders as a
small animated sprite whose pose reflects its status.

## 4. Steer it, or require your sign-off before it finalizes

Redirect a still-running role from the dashboard's steer panel, or:

```bash
uv run cuttlefish steer <task-id> "actually use .dockerignore instead" --role builder
```

A steering message takes effect at the boundary between delegation rounds,
not mid-flight — it waits for whatever round is currently running to
finish first. This is a deliberate limit
([ADR-0008](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0008-steering-is-a-round-boundary-redirect-not-a-mid-flight-interrupt.md)),
not a missing feature.

For a formal review gate instead of an optional redirect — a round never
finalizes until you decide — start the project (or task) with
`--require-approval`, then approve or reject from the dashboard's panel, or:

```bash
uv run cuttlefish approve <task-id> --role builder                        # finalize it
uv run cuttlefish approve <task-id> --role builder --reject "use .dockerignore instead"  # one more round
```

## 5. Answer an agent that needs you

In **Ask first** and **Standard**, a kopicode agent that wants to run a command nothing approves
stops and waits. It shows up under **Needs you** (a tab on the project, and a destination in the
rail with a count) with the command, why it stopped and a countdown. Choose **Allow once**,
**Always allow** (the start of the command, which applies to the running team at once and is saved
to the project's own commands for later starts), or **Deny**. If you do nothing, the request denies
itself when `CUTTLEFISH_REQUEST_WINDOW` runs out (ten minutes by default) and the agent carries on
with a refusal. Restarting the daemon abandons a waiting request; it is not asked again.

When an agent asks you a question with kopicode's `ask` tool (kopicode v0.4.0 or later), it is a
**Question** card with a text box: **Send answer** gives the agent your words, **Decline** (or no
answer in time) tells it nobody is there and it carries on.

every backend can pause in the daemon: Codex over `codex app-server` and Claude Code over stream-json
(the defaults; with `CUTTLEFISH_CODEX_TRANSPORT=exec`, `CUTTLEFISH_CLAUDE_CODE_TRANSPORT=print`, or inside
a sandbox provider they refuse the command and say so in the activity log). A team started with `cuttlefish run` or `run-team` has no inbox, so it refuses too. The same
requests are available over HTTP (see the [CLI reference](cli-reference.md)).

A **Stuck** card is different: it has no buttons. A kopicode agent whose shell commands fail five
times in a row because a tool or package is missing (`No module named`, `command not found`, ...)
is stopped, and the card shows its last failing command. Nothing is waiting for an answer. Fix the
environment, then steer that role from the project page; the card ends when you do.
`CUTTLEFISH_STUCK_THRESHOLD` changes the five, and `0` turns the check off.

## 6. Reach it from another device

```bash
cuttlefish serve --tailscale
```

Needs `CUTTLEFISH_SERVE_PASSWORD` set (a non-loopback bind switches from
the loopback default's shared static token to real password/session auth).
Opening the printed URL directly on your tailnet needs no extra flag. Keep
it running across crashes and reboots with `make install-systemd-service` —
it reviews and prints the exact next commands, never starts anything on its
own.

See [CLI reference](cli-reference.md) for every command and flag, and
[Architecture](architecture.md) for how the pieces fit together.
