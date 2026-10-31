---
icon: lucide/terminal
---

# CLI reference

Every subcommand of `cuttlefish` (`uv run cuttlefish --help`). Flags shown
here are the ones worth knowing about — pass `--help` on any command for
the exhaustive list.

## `cuttlefish run`

Submit a single task and block until it finishes.

```bash
uv run cuttlefish run "run the test suite" --allow "go test" --allow "npm test"
```

| Flag | What it does |
|---|---|
| `--root ROOT` | The checkout to delegate against (default: CWD). |
| `--allow CMD` | One shell command the delegation may run inside `--root`, shell-quoted. Repeatable. Added to the built-in dev presets (see [Default permissions](#default-permissions)). |
| `--token-budget N` | Working-memory handover threshold, in estimated tokens. |
| `--project NAME` | This task's secrets scope. Default: `--root`'s own directory name. |
| `--secret NAME` | One named secret this task's backend may read (needs `CUTTLEFISH_SECRETS_KEY`). Repeatable. |
| `--steerable` | Open a local control API so `cuttlefish steer` can redirect this run. |
| `--require-approval` | A round never finalizes on its own — blocks until `cuttlefish approve` decides it. Implies `--steerable`'s control API. |
| `--max-tokens N` / `--max-cost-usd USD` | Force a decision once cumulative usage crosses this ceiling — the same review gate `--require-approval` uses. |
| `--resume ID` | Continue an unfinished run (e.g. after `kill -9`) instead of starting a new one. Repeat the original command's arguments exactly; the round that was in flight starts over, finished rounds are kept. Without it, a rerun warns about unfinished runs in the directory. Also on `run-team`. |

## `cuttlefish run-team`

Run several named roles concurrently against one project.

```bash
uv run cuttlefish run-team \
  --role builder:"implement the login form" \
  --role reviewer:"review the last commit for style issues"
```

`--role-backend NAME=BACKEND` (repeatable) runs that role through
`kopicode`, `claude-code` or `codex` instead of `CUTTLEFISH_AGENT_BACKEND`, so
one team can mix agents. Every named backend's CLI must be on `PATH` before
the team starts.

`--limit KEY=N` (repeatable) sets a limit for every role and `--role-limit NAME:KEY=N` one role's,
over it: `max_turns`, `session_token_budget`, `context_limit_percent`, `round_timeout_minutes`,
`max_continuations` and `max_idle_rounds`. These are the dashboard's project and role limits (Team
tab); what a team does not set is read from the `CUTTLEFISH_*` variables below. A bad key or a value
out of range stops the command before anything starts. Only kopicode takes the turn, token, context
and time limits. On a resume, repeat the same flags.

`--role NAME:TASK_TEXT` is repeatable and required at least once. Every
other flag from `run` applies team-wide (one `--root`, one `--allow` list
shared by every role, one steerable/approval/budget setting applying to
each role independently).

## `cuttlefish show TASK_ID`

Render one task's (or team's) full episodic record — every round, every
delegation outcome, every tool call, in the one place everything is
written. `TASK_ID` is the id `run`/`run-team` printed at start.

## `cuttlefish steer TASK_ID MESSAGE [--role ROLE]`

Redirect a still-running `--steerable` task or team role. `--role` is
required for a team (which role to steer), omitted for a plain `run` task.
Takes effect at the next round boundary, not mid-flight. (The dashboard's steer is different for a Codex role: it goes into the turn that is running, [ADR-0033](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0033-a-codex-turn-in-flight-takes-a-steer-through-turn-steer.md).)

## `cuttlefish approve TASK_ID [--role ROLE] [--reject COMMENT]`

Decide on a still-running `--require-approval` task or team role. Omit
`--reject` to approve (no comment needed); pass `--reject "<comment>"` to
send it back for another round with that feedback attached.

## `cuttlefish secrets`

Manage the project-scoped, encrypted-at-rest secrets store.

| Subcommand | What it does |
|---|---|
| `generate-key` | Print a fresh `CUTTLEFISH_SECRETS_KEY`. |
| `set --project NAME KEY` | Set a secret's value (prompted, or piped via stdin). |
| `get --project NAME KEY` | Print a secret's value. |
| `delete --project NAME KEY` | Delete a secret. |
| `list --project NAME` | List a scope's secret names — never values. |

These read and write `.cuttlefish/secrets.db` in the current folder. The dashboard's secrets live in one central file,
`~/.cuttlefish/secrets.db`, so a shared secret really is shared (see [Secrets in the dashboard](#secrets-in-the-dashboard)).

## `cuttlefish init [--root DIR] [--name NAME] [--backend B] [--role NAME[:PERSONA]]...`

Guided first-run setup. Picks the backend (`--backend`, else
`CUTTLEFISH_AGENT_BACKEND`, else the first of `kopicode`/`claude`/`codex` on
`PATH`), checks that `uv`, the agent CLI and a plausible login are present
(best effort: an env credential or the CLI's own login directory, never a
credential file's contents), registers `--root` (default `.`) as a project
with `builder` and `reviewer` roles unless `--role` overrides them, and
prints the exact `cuttlefish run` command to try next. Re-running on an
already-registered root reuses it. Exits 2 if a check fails.

## `cuttlefish projects`

Manage the `Project` registry the fleet daemon reads from
(`~/.cuttlefish/projects.db`). `projects add --backend B` sets a project's
default agent backend and `--role-backend NAME=B` a single role's; resolution is
role, then project, then `CUTTLEFISH_AGENT_BACKEND`. The dashboard's register
form takes the same (`name@backend: persona` per role). `--terminal-command CMD` runs a command in the
project's tmux session instead of a known backend, with no live status, chat or Needs you for it (ADR-0047).

```bash
uv run cuttlefish projects add \
  --name demo --root /path/to/checkout \
  --role builder:"careful, writes tests first" \
  --allow "go test" --max-tokens 200000
```

| Subcommand | What it does |
|---|---|
| `add` | Register a project — `--name`/`--root` required; `--secrets-scope`, `--template NAME` (a built-in team, below) or `--role NAME[:PERSONA]` (repeatable), `--allow CMD` (repeatable), `--max-tokens`/`--max-cost-usd` optional. |
| `list` | List every registered project. |
| `pr` | Open a pull request for the project's goal branch with `gh` and print its URL (`--push` pushes the branch to `origin` first). Needs `gh` installed and logged in; never merges ([ADR-0049](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0049-a-goal-branch-opens-a-github-pull-request-through-the-machines-own-gh-and-never-merges.md)). |
| `remove` | Deregister a project — never touches its files. |

## `cuttlefish schedule`

Ask a project's builder for a goal on a repeat; `cuttlefish serve` starts each one when it is due (see
[Scheduled goals](quickstart.md#scheduled-goals)). Output is one JSON object per line. A running daemon sees a change
at its next pass, so nothing needs restarting.

| Subcommand | What it does |
|---|---|
| `add PROJECT_ID --goal TEXT --recurrence R` | Add a schedule. `R` is `every <N>m`, `every <N>h` or `daily HH:MM`, always UTC. |
| `list [--project ID]` | List schedules, soonest first, with `next_due`, `last_run` and `last_result`. |
| `remove ID` | Delete a schedule. |
| `enable ID` / `disable ID` | Turn one on (counting from now) or off. |

## `cuttlefish serve`

Start the fleet daemon: launches and owns every registered project's team
concurrently, in one process. On a loopback bind it prints a URL and a token. The token is
saved in `~/.cuttlefish/serve-token` (mode 0600) and reused on every start, so a restart does not
sign an open dashboard out; delete the file to rotate it. A non-loopback bind uses the password
instead. A session lasts 12 hours after its last request and slides forward while you use it (up to seven
days from the password, [ADR-0038](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0038-a-password-session-slides-forward-while-it-is-used.md)).
Sessions do not survive a restart: the dashboard asks you to sign in again and carries on where it was.

| Flag | What it does |
|---|---|
| `--host HOST` | Loopback by default. A non-loopback host (LAN IP, Tailscale address, `0.0.0.0`) requires `CUTTLEFISH_SERVE_PASSWORD` and switches to password/session auth. |
| `--port PORT` | Auto-picks a free port if the default is taken. |
| `--tailscale-https` | Keep the daemon on loopback and print the two `tailscale serve` commands that give the dashboard an https address and the relay a `wss` address, so a phone can pair (see [Pairing over Tailscale https](#pairing-over-tailscale-https)). Needs `CUTTLEFISH_SERVE_PASSWORD`. Turns the relay on. Cannot be combined with `--tailscale`. |
| `--tailscale` | Bind directly to this machine's own Tailscale IPv4 address — the recommended remote-access path. Overrides `--host`. |
| `--relay URL` | Also dial out to a relay (`ws://host:port`, run by `cuttlefish relay`) and print a link that opens the dashboard through it, end-to-end encrypted. Preview, loopback only: see [the relay](#cuttlefish-relay). Default: `$CUTTLEFISH_RELAY_URL`. |
| `--allow-origin ORIGIN` | A browser origin the dashboard may be served from in non-loopback mode. Repeatable. |
| `--browse-root PATH` | A folder the dashboard's folder picker may browse: its subfolders only, with hidden folders and links out of the tree excluded. Repeatable. Default: your home directory. Registering a project is not limited to these. In non-loopback mode, set it to a workspaces folder rather than your home. |
| `--dashboard-dir PATH` | Serve the dashboard's own production build from this directory, same-origin with the JSON API. Default: auto-detect `./frontend/dist`. |

## `cuttlefish relay`

**Preview (CUT-119 and CUT-120, [ADR-0037](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0037-a-relay-carries-end-to-end-encrypted-traffic-between-a-browser-and-the-daemon.md),
[ADR-0039](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0039-a-device-pairs-once-with-a-confirm-on-the-pc-and-the-daemon-holds-it-to-a-tier.md)).**
A headless server, with no screens, that passes encrypted traffic between a browser and a daemon. The daemon
dials out to it (`cuttlefish serve --relay ws://127.0.0.1:8450`), and a browser that has paired with the daemon runs a
Noise handshake with it *through* the relay. The relay sees which rooms exist, who connects and leaves, and how many
bytes move. It does not see paths, commands, output or the terminal.

| Flag | What it does |
|---|---|
| `--host HOST` | Loopback by default. Without `--db` it refuses anything else, because an open relay has no accounts. |
| `--port PORT` | Default 8450. |
| `--db PATH` | Run the **hosted, multi-user** relay with its accounts in this SQLite file ([ADR-0043](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0043-the-hosted-relay-is-multi-user-with-accounts-that-own-rooms-and-hold-no-project-data.md)). The owner's sign-in secret (32 or more characters) comes from `CUTTLEFISH_RELAY_OWNER_SECRET`. Default `$CUTTLEFISH_RELAY_DB`. |
| `--pages DIR` | Also serve this dashboard build (`frontend/dist`) from the relay's own https origin, with a Content-Security-Policy. This is what lets a phone with no VPN open the dashboard. |
| `--trust-forwarded` | Count limits against the last `X-Forwarded-For` address. Only behind a proxy that sets it, with the relay's port reachable from nowhere else. |
| `--insecure-cookies` | Do not mark the session cookie `Secure`. Plain http on loopback only. |

### The hosted relay

With `--db` a room belongs to an account, and a daemon must present the room's registration token. A second daemon with
the wrong token cannot replace a connected one (an open relay lets it). Per address, a relay allows 40 open connections,
60 new connections a minute, 10 sign-in attempts a minute and 10 wrong daemon tokens a minute; an account owns up to 5
rooms. The relay keeps accounts, rooms (a hash of the token), sessions and daily byte counts, and no project data, model
keys or agent credentials.

Make a room and its token on the machine that holds the database, then give both to the PC:

```bash
cuttlefish relay-admin create-room --db /data/relay.db --label "Work PC"    # prints the room id and the token once
CUTTLEFISH_RELAY_ROOM=<room> CUTTLEFISH_RELAY_TOKEN=<token> cuttlefish serve --relay wss://relay.example.com
```

`--relay-room` (or `CUTTLEFISH_RELAY_ROOM`) names the room; the token is read only from `CUTTLEFISH_RELAY_TOKEN`, never
the command line. The pairing link then opens the relay's own page. `deploy/relay/` in the repository has the
Dockerfile, the compose file (the relay and Caddy as two containers; see [Hosting the relay](hosted-relay.md)) and the
Caddyfile that puts one shared password in front of the whole site until the outside security review
is done; the daemon's websocket is the one route that skips it, because its room token gates it.

### Pairing a phone

On the PC's dashboard open the account menu, then **Devices**, then **Make a pairing link**. It shows a link and a QR
code that work once and expire after five minutes. Open the link on the device, name it and tap **Pair**.

**The page the device opens must be a secure address**: https, or `http://localhost` on the same machine. The encryption
uses the browser's WebCrypto, which browsers switch off on plain http, so a plain-http address such as
`http://100.x.y.z:8431` (a Tailscale address) **cannot pair**: the page says so. Checked in Chromium and Firefox on a real
Tailscale address. A phone on Tailscale can still sign in with the password over that address, as before. Pairing from a
phone needs an https page: Tailscale's own https addresses ([below](#pairing-over-tailscale-https)), or the hosted relay
when it exists. On the PC itself, `http://localhost` works.

#### Pairing over Tailscale https

1. In the Tailscale admin console, under DNS, turn on **HTTPS Certificates**. This publishes the machine's name, and so
   your tailnet's name, in public certificate transparency logs.
2. Start `CUTTLEFISH_SERVE_PASSWORD=… cuttlefish serve --tailscale-https`. It reads the machine's name from
   `tailscale status --json` (and says what to do if HTTPS is off), keeps the daemon on `127.0.0.1`, turns the relay on and
   prints the commands below. It does not run them, because `tailscale serve` changes this machine's Tailscale serve
   configuration.
3. Run `cuttlefish relay` (it stays on loopback), then `tailscale serve --bg --https=443 http://127.0.0.1:8431` and
   `tailscale serve --bg --https=8443 http://127.0.0.1:<relay port>`. Tailscale needs root for this, or `sudo tailscale set
   --operator=$USER` once. `tailscale serve reset` undoes it.
4. Open `https://<machine>.<tailnet>.ts.net/` on the PC (from WSL2 only a browser inside WSL, such as one run through WSLg, can reach it; a Windows browser should use `http://127.0.0.1:<port>`), sign in, and make a pairing link. The address box under Devices already holds the https address. The link carries the https page
   and a `wss://<machine>.<tailnet>.ts.net:8443` relay address. The phone needs the Tailscale app on the same tailnet.

`make phone HTTPS=1` does steps 2 and 3 for you against a scratch home and a fake agent, asking before it changes the
Tailscale serve configuration.

The dashboard's port is the one you asked for: with `--tailscale-https` it is never moved to a free one, because the
`tailscale serve` command names it. Checked in Chromium and Firefox through real `tailscale serve` addresses (pair, match the
codes, confirm, use the dashboard through the relay). Not yet checked on a real phone or in Safari.

When the device taps Pair, it and the PC both show the same six-digit code. **Confirm on the PC only if they match.**
Until you do, the device can reach nothing. After that the phone keeps its own key and
secret and uses the relay whenever its page is opened.

### What a device may do

A new device is **Approve**: it can read, answer Needs you, steer, start and stop, and remove a device. **View** is read
only. **Terminal** adds the agent's terminal and **Admin** adds settings, secrets, folders, projects and devices. You
raise a device to Terminal or Admin under Devices, and the daemon enforces each tier on every route. The PC's own session
is not limited. **Remove** on a device signs it out at once, and its next connection is refused; a paired device can remove
another one, so a lost phone can be cut off from a second.

A device below Approve cannot list devices, so its Devices page shows its own name and tier instead, and the tier updates
within a few seconds of a change on the PC. A pairing link works once: opened again, it says it was already used or has
expired.

**Remove ends the device's paired connection, not a password session.** If the same browser also signed in with the
dashboard password, that session stays valid until it expires or the daemon restarts, and it is not held to a tier. Only
the paired path is tiered, so to test a tier, use a browser that has never signed in with the password.

### What this preview is not

An open relay (no `--db`) has no accounts, so anyone who can reach it can claim a room; it runs on loopback only. The
hosted relay above serves the page itself, but it has one account and sits behind a shared password until the outside
security review. There is no
WebAuthn step-up for Terminal and Admin (it needs https), so those tiers stay on until you turn them off. Devices are
kept in `~/.cuttlefish/devices.db`, and the daemon's own key in `~/.cuttlefish/relay-key`, both mode 0600.

## `cuttlefish doctor`

Checks what is set up and what is quietly wrong, and exits non-zero only for a problem that
stops a start. It reports each backend's binary (location and version), each credential by
name only (set, not set, or still the `.env.example` placeholder; the secrets key is also
checked for being a real Fernet key), `PATH` entries that mislead an agent (cuttlefish's own
venv, Windows `/mnt/...` directories under WSL), the log file, and each registered project
(root exists, an empty `.cuttlefish/secrets.db` left by a failed start, which daemon variables an agent is not given, and what its files say it needs: `Python (uv, wants 3.12, .venv present)`). Values are never printed.

## `cuttlefish soak run` and `soak score`

A harness that scores a long unattended Autopilot run, so Autopilot is trusted on evidence and not on a guess. It is a client
of an already-running `cuttlefish serve`, **a scratch one on its own port** (it refuses port 8420, a person's usual daemon,
unless you pass `--allow-default-port`). It gives the project's builder a goal with Autopilot on, approves the plan once,
then samples Autopilot, Needs you, the thread and the timeline until the run ends or `--hours` is reached, when it stops
Autopilot. It answers no permission and says nothing to the builder, so what it scores is what Autopilot managed alone.

```bash
uv run cuttlefish soak run --base-url http://127.0.0.1:8431 --token <token> --project <id> \
  --goal "Build the thing in SPEC.md" --hours 8 --out soak/2026-10-24-baseline
uv run cuttlefish soak score soak/2026-10-24-baseline/run.jsonl      # score a saved run again
```

A real run uses the model login or key the daemon has, so it prints a size estimate and asks you to type `yes` first (`--yes`
skips the question; without a terminal it is required). `--out` gets `report.md`, `score.json` and `run.jsonl` (written as
the run goes, so a crash of the driver loses nothing; the builder carries on in the daemon).

The report gives useful time (running with nothing waiting, minus **stalled** time when the token count stood still for
10 minutes), time blocked on a permission, time stuck, human interventions (a message, a resume or a permission someone else
answered; approving the plan is counted apart), steps ticked, nudges, commits, handoffs (context refreshes), tokens per day,
and a cost per day if you give `--usd-per-mtok`. Tokens are Autopilot's own figure (cache reads left out). `--arm` labels
the run (`baseline` is Autopilot alone); the manager arm waits for the manager.

## `cuttlefish mcp`

Run an MCP server (stdio transport) wrapping an already-running
`cuttlefish serve`'s own HTTP API — a separate client process, useful for
driving the fleet from an MCP-aware agent/editor rather than the CLI or
dashboard directly.

```bash
uv run cuttlefish mcp --base-url http://127.0.0.1:8420 --token <token>
```

Twenty tools. Run and watch: `list_projects`, `get_project`, `register_project` (also takes
`mode` and `template`), `start_project`, `stop_project`, `steer_project`, `approve_project`,
`get_events`. The agent in tmux: `get_agent_session`, `start_agent_session`, `stop_agent_session`. Environment: `get_project_environment` (what the project's files say it needs:
ecosystem, package tool, version, whether its own install is present; nothing is run). Permissions: `get_permissions` (modes, command groups, the never-allowed list and the
per-backend notes), `list_builtin_roles`, `list_templates`, `set_project_mode` and `update_roles`
(which replaces the whole role list, including each role's `access`); both apply the next time the
team starts. Spend: `set_project_budget` (`max_tokens` and/or `max_cost_usd`; a field left out is
unset). `start_project` refuses a role the project never registered (a 400 that lists its roles), and
`register_project` and `projects add` refuse a root another project already uses. Needs you: `list_requests` (the fleet, or one project) and `answer_request`
(`allow_once`, `allow_always` with a `rule`, or `deny`; `answer` with `text`, or `decline`, for a
question), which wrap the
[request routes](#needs-you-requests-over-http) and keep their behaviour: the same answer twice is
fine, a different or late one is a 409, an unknown request a 404 and a refused rule a 422. Answering
lets an agent run a shell command, so it is as weighty as `start_project`.

### The manager's tools

`cuttlefish mcp --manager --base-url <url>` serves a smaller set for a manager agent that watches many projects
(ADR-0046, ADR-0050). It uses the daemon's manager credential from `~/.cuttlefish/manager-token` (made by the daemon,
mode 0600, never printed; delete it to rotate), not `--token`. The daemon holds that credential to the Approve tier and
to the routes these tools call, so it is refused on settings, modes, secrets, devices, the terminal and starting or
stopping a team (a 403).

| Tool | What it does |
|---|---|
| `list_attention` | Every pending request across projects, oldest first. A permission shows its command, directory, role and the rule that did not match; other text is clipped and marked untrusted. |
| `project_digest` | A compact digest: status, last events, blockers, pending requests, spend. No code, output or secrets. |
| `recent_answers` | How a project's last requests ended, and the option a person chose for a decision. |
| `answer_attention` | `allow_once` or `deny` only. Never an always-allow rule, never a decision. A command on the never-allowed list is denied whatever the answer. |
| `message_agent` | Steers a running role, as a person's steer does. |
| `raise_decision` | Asks the person to choose: a question, two to four options and a recommended one. It waits with no timer; the dashboard shows it as a Decision card. |

Every answer is journaled with who gave it (`person` or `manager`) and the caller's tier. A manager cannot answer or
message in the project named by `CUTTLEFISH_MANAGER_PROJECT` (the project hosting its own session).

## Configuration

| Variable | Default | What it does |
|---|---|---|
| `CUTTLEFISH_AGENT_BACKEND` | `kopicode` | Which coding-agent backend a delegation runs through: `kopicode`, `claude-code`, or `codex`. One choice per process — every project and role a given `cuttlefish serve` runs shares it. |
| `CUTTLEFISH_KOPICODE_BIN` / `CUTTLEFISH_CLAUDE_CODE_BIN` / `CUTTLEFISH_CODEX_BIN` | `kopicode` / `claude` / `codex` | Path to that backend's binary, when selected. |
| `CUTTLEFISH_CLAUDE_CODE_TRANSPORT` | `stdio` | `print` runs Claude Code as the one-shot `claude -p` (allow patterns, nothing to ask a person) instead of keeping a stream-json session open. |
| `CUTTLEFISH_KOPICODE_TRANSPORT` | `serve` | `print` runs kopicode as the one-shot `kopicode run --print` (a policy file, nothing to ask a person) instead of `kopicode serve`. It is the only kopicode transport that can continue a session across rounds with `CUTTLEFISH_RESUME=1`. |
| `CUTTLEFISH_CODEX_TRANSPORT` | `app-server` | `exec` runs Codex as the one-shot `codex exec` (coarse sandbox, nothing to ask a person) instead of `codex app-server`. |
| `CUTTLEFISH_KOPICODE_PROVIDER_URL`, `CUTTLEFISH_KOPICODE_MODEL` | unset | Send kopicode's model traffic to an OpenAI-compatible endpoint (a local Ollama at `http://127.0.0.1:11434/v1`, llama.cpp, vLLM, a proxy) instead of OpenRouter, with kopicode's `--provider-url`; the model id the endpoint serves is required with it. Needs a kopicode that lists `provider_url.flag`, works on both the `serve` and `print` transports, and is refused with a sandbox provider (a container cannot reach a host-local endpoint). The endpoint's key, if it wants one, is `KOPICODE_PROVIDER_API_KEY` (optional on loopback); `OPENROUTER_API_KEY` is not passed to kopicode then. kopicode runs these sessions **unpinned** and tuned for no local model, so use it to try the plumbing or a local model, not to compare arms. |
| `CUTTLEFISH_RESUME` | off | `1` keeps one conversation per role (or per one-shot run) for Codex, Claude Code and kopicode, in `<root>/.cuttlefish/conversations.json`, and continues it with the tool's own resume (`thread/resume`, `--resume`, `-resume`), so a later prompt remembers the earlier ones, even after a restart. Only over `app-server`, `stdio` and kopicode's `print` transport (`CUTTLEFISH_KOPICODE_TRANSPORT=print`), not the one-shot transports. The default kopicode `serve` transport continues it with `session.start`'s `resume` when the kopicode lists the `session.resume` feature (`kopicode version --json`; KOP-207), and starts a new session each round on an older one or inside a sandbox provider. A handover checkpoint starts a fresh conversation on purpose. `CUTTLEFISH_CODEX_RESUME=1` still turns it on for Codex alone. Experimental: the session-first path (ADR-0032) will make it the default. |
| `CUTTLEFISH_CODEX_MODEL`, `CUTTLEFISH_CODEX_EFFORT` | unset | The model (`--model`) and reasoning effort (`minimal`, `low`, `medium`, `high`) for the Codex backend, so a team can use a small model on your ChatGPT subscription without editing `~/.codex/config.toml`. Unset leaves Codex's own configuration alone. A daemon run as another HOME also needs `CODEX_HOME` pointing at the Codex login, named in `CUTTLEFISH_AGENT_ENV_PASSTHROUGH`. |
| `CUTTLEFISH_SANDBOX` | `none` | Real containment for the delegation: `none`, `container` (local Docker), or `e2b`. |
| `CUTTLEFISH_AGENT_ENV_PASSTHROUGH` | unset | Names (comma-separated, `*` ends a prefix) to pass to agents on top of the allowlist (see What an agent's environment is). |
| `CUTTLEFISH_CREDENTIAL_BROKER` | unset | `1` makes `cuttlefish serve` keep each agent's model API key itself and lease the agent a short-lived token for a loopback proxy instead, so a command the agent runs finds no real key. Claude Code (a key from the environment or Secrets) and Codex (only an `OPENAI_API_KEY` set in Secrets, because Codex ignores one in the environment under a ChatGPT login); a login file is not a key and is not brokered. A sandbox run is unchanged. |
| `CUTTLEFISH_KEEP_WINDOWS_PATH` | unset | `1` keeps WSL's `/mnt/...` entries on an agent's `PATH`; they are dropped by default. |
| `CUTTLEFISH_PREPARE_TIMEOUT` | `900` | Seconds one dependency-install step may run before it is killed (see Installing dependencies). |
| `CUTTLEFISH_LOG_LEVEL` | `INFO` | Log level for `cuttlefish serve`: the terminal and `~/.cuttlefish/logs/cuttlefish.log` (rotating, 5 MB x 5). `DEBUG` adds every tool call and permission decision; an unrecognised value falls back to `INFO` and says so. |
| `CUTTLEFISH_STUCK_THRESHOLD` | `5` | How many shell commands in a row may fail on the project's environment (`No module named`, `command not found`, `ENOENT`, `Cannot find module`, ...) before cuttlefish stops a kopicode agent instead of letting it run to `max_turns`. `0` turns it off. Claude Code and Codex are not watched. |
| `CUTTLEFISH_MAX_TURNS` | `100` | Turns a kopicode round may take before it stops (kopicode v0.4.0 or later; an older one keeps its own 20). These are the daemon's defaults: a project, and a role in it, can set its own in the dashboard (Team tab) or over HTTP (`GET /api/limits`, `PATCH /api/projects/{id}/limits`); role over project over this variable over the built-in value. |
| `CUTTLEFISH_SESSION_TOKEN_BUDGET` | `5000000` | Tokens one kopicode round may spend, counting the history resent on each request; `0` is unbounded. |
| `CUTTLEFISH_MAX_CONTINUATIONS` | `20` | How many times a team role carries on by itself, with a fresh session and the latest handover, after a round stops on turns, tokens or time, or after a command was refused, and was not stuck. `0` makes that stop a failed round, as before. |
| `CUTTLEFISH_ROUND_TIMEOUT` | `7200` | Seconds one kopicode round may run before cuttlefish cancels it; the role then continues from the handover like any other round that ran out of room. `0` is no limit. A round waiting on you in Needs you counts. |
| `CUTTLEFISH_MAX_IDLE_ROUNDS` | `3` | How many rounds in a row may end (out of room, or on a refused command) without changing a file before the role is held and a Needs-you card says so. A steer starts the count again. `0` turns it off. |
| `CUTTLEFISH_CONTEXT_LIMIT_PERCENT` | `75` | How full a kopicode round's context may get, as a percentage of the model's window, before cuttlefish ends the round and the role continues from the handover with a fresh context. Needs kopicode v0.4.0 and a model whose window kopicode knows; otherwise nothing happens. `0` turns it off; above `95` counts as `95`. |
| `CUTTLEFISH_REQUEST_WINDOW` | `600` | Seconds you have to answer a Needs-you request (a command a kopicode agent wants to run that nothing approves) before it is denied, 10 to 86400. For a Claude Code session in tmux nothing is denied when it runs out: the terminal's own dialog still asks. cuttlefish asks kopicode for `--consent-timeout` when `serve --help` lists it (kopicode v0.3.0 and later). An older kopicode denies after its own fixed 60 seconds, so there you get 45. |
| `CUTTLEFISH_SECRETS_KEY` | unset | Enables the project-scoped secrets store. Unset means `cuttlefish secrets`/`--project`/`--secret` and the dashboard's secrets routes are unavailable. |
| `CUTTLEFISH_SERVE_PASSWORD` | unset | Required for any non-loopback `cuttlefish serve` bind. |
| `CUTTLEFISH_RELAY_URL` | unset | The relay `cuttlefish serve` dials out to, as `--relay` does (preview). |
| `CUTTLEFISH_RELAY_ROOM`, `CUTTLEFISH_RELAY_TOKEN` | unset | The room id and registration token a hosted relay gave this PC. |
| `CUTTLEFISH_RELAY_DB`, `CUTTLEFISH_RELAY_OWNER_SECRET` | unset | For `cuttlefish relay`: the accounts file and the owner's sign-in secret. |
| `CUTTLEFISH_MCP_BASE_URL` / `CUTTLEFISH_MCP_TOKEN` | unset | Defaults for `cuttlefish mcp --base-url`/`--token`. |

A real run needs a model credential for the agent backend that is selected (cuttlefish makes no model call of
its own): `OPENROUTER_API_KEY` for kopicode, Claude Code's own login or
`ANTHROPIC_API_KEY`, and `codex login` for Codex (`codex exec` ignores an
ambient `OPENAI_API_KEY`, ADR-0018).


## Installing dependencies before a team starts

cuttlefish can install a project's dependencies itself, before the team's first round and outside any
agent's turns, so an agent does not spend them discovering that `.venv` or `node_modules` is missing. It
runs `uv sync` (`--frozen` when there is a `uv.lock`, so your lockfile is never rewritten; `uv venv` plus
`uv pip install -r requirements.txt` for a requirements project) or `npm ci`, `pnpm install
--frozen-lockfile`, `yarn install --frozen-lockfile` (`--immutable` for Yarn Berry), `bun install
--frozen-lockfile`, from the project folder (or the subfolder the project is in), with cuttlefish's own venv removed from the environment. It also
runs `poetry install` and `pipenv sync` (or `pipenv install` with no `Pipfile.lock`), both told to keep the
environment in the project's `.venv`; `go mod download`; `cargo fetch` (`--locked` with a `Cargo.lock`);
`bundle install` (frozen with a `Gemfile.lock`); and for Java `mvn -B dependency:resolve` or Gradle's
`dependencies` task, using the project's `./mvnw` or `./gradlew` when it has one. Go, Rust, Java and Ruby keep
their downloads outside the project, so there is no folder to look at: they are fetched once, and again when
their files change. A project cuttlefish cannot install (a `pyproject.toml` with no requirements file and no
lock, say) is listed with the reason.

A step runs when the project's own install is missing, or when its manifest, lockfile or version hint
changed since cuttlefish last installed (it remembers in `.cuttlefish/env.json`, only after a success). An
install you made yourself is trusted until those files change. Installing runs the project's own install
scripts, so it is a setting, `ask` (the default), `auto` or `off`, and the start call can answer it:

- `POST /api/projects/{id}/start` takes `"prepare": "yes"` (install first) or `"skip"` (start without).
  Left out, `auto` installs, `off` never does, and `ask` answers **409** naming what is stale and the two
  values. The MCP `start_project` tool takes the same `prepare` argument.
- The install is journaled (`Installing`, `Install done` in Recent activity, with the exit code and the
  end of the output). If it fails, every role shows failed with why and no round starts. Stopping the team
  while it installs kills the install. `CUTTLEFISH_PREPARE_TIMEOUT` (seconds, default 900) bounds each step.

In the dashboard: with **Ask me**, Start shows what would run (each command, in the project folder) and offers
*Install and start*, *Install, and do this automatically*, *Start without installing* or *Cancel*. The
project's Environment card has the three-way setting, and while an install runs the Overview says so and
*Stop team* cancels it.

## What an agent's environment is

An agent's shell does not inherit the daemon's environment. It gets an allowlist: `HOME`, `USER`, `SHELL`, `TMPDIR`,
`LANG` and `LC_*`, `TERM`, `XDG_*`, proxies and CA bundles (`HTTPS_PROXY`, `SSL_CERT_FILE`, ...), and toolchain
locations (`GOPATH`, `CARGO_HOME`, `JAVA_HOME`, ...), plus what its own backend reads (`KOPICODE_*`, `CLAUDE_*`,
`CODEX_*` and their base URLs) and the credentials it declares. The daemon's own settings (`CUTTLEFISH_*`), everything
loaded from `.env` and everything `uv run` added are not passed. `PATH` has cuttlefish's own venv and, on WSL, the
`/mnt/...` Windows directories removed (`CUTTLEFISH_KEEP_WINDOWS_PATH=1` keeps them), and the project's own `.venv`
(with `VIRTUAL_ENV`) and `node_modules/.bin` come first when they exist. Each role's brief opens with a short
`Environment:` note saying so.

A variable an agent needs and does not get (for example `SSH_AUTH_SOCK` for `git` over SSH, or `AWS_*` for Claude
Code on Bedrock) goes in `CUTTLEFISH_AGENT_ENV_PASSTHROUGH`. `cuttlefish doctor` lists the names that are withheld from this shell's environment, never the values (credentials, SSH and
cloud names first; `cuttlefish doctor --all-env` lists them all).

### The credential broker

With `CUTTLEFISH_CREDENTIAL_BROKER=1`, `cuttlefish serve` keeps an agent's own model API key itself. The agent is given
a base URL for a proxy on `127.0.0.1` and a random token in place of the key; the proxy adds the real key and forwards
the call. A command the agent runs then finds no real key, and a leaked token works only on this machine, only until the
round ends. Claude Code is brokered for a key from the environment or Secrets, Codex only for an `OPENAI_API_KEY` set under
Secrets (it ignores one in the environment under a ChatGPT login). Not covered: a login file, a run inside a sandbox
provider, kopicode (it already keeps its key from its shell), `cuttlefish run`, and `CLAUDE_*` variables passed through to
Claude Code. `cuttlefish doctor` shows whether it is on.

### What the next round is told

When a round runs out of room, the role carries on from a handover. kopicode (turn cap, token budget, a full context)
and Codex over `app-server` (its round token limit or round time limit) are asked to write their own handoff, and the
next round is told what they wrote. Claude Code over stream-json is asked when a round ends on its time limit. The one-shot
transports and a sandbox provider cannot be asked, and get a digest cuttlefish reads from the journal: the files changed, the agent's last words, what failed or was refused
and what you said. A digest says it names no remaining work. cuttlefish makes no model call to write either. ADR-0042.

## Secrets in the dashboard

The side bar's **Secrets** screen manages the secrets shared with every project (add, replace, remove; the remove dialog says how
many projects lose it).

A project's **Secrets** tab lists its secrets (a lock, the name and a fixed run of dots: values are never shown), adds and replaces
one in a dialog, and removes one after a confirmation. Secrets shared with every project show read-only below. An agent key is
marked, with a line saying who can see it. With no secrets key the tab explains how to turn secrets on and offers no form.

Secrets set through the daemon are stored encrypted in `~/.cuttlefish/secrets.db`, one file for every project, under
the daemon's `CUTTLEFISH_SECRETS_KEY`. A team start hands the agents every secret its project can use (its own and the
shared ones) as environment variables; a change applies to the next start. A secret in the project folder's own
`.cuttlefish/secrets.db` (what `cuttlefish secrets` wrote) is still read, after the central store.

| Route | What it does |
|---|---|
| `GET /api/projects/{id}/secrets` | `{"enabled", "project": [{"name", "kind"}], "shared": [...]}`. `kind` is `credential` when a backend runs on that name (`OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`), else `secret`. Names only. |
| `PUT /api/projects/{id}/secrets/{name}` | Body `{"value": "..."}`. Replaces any value. The name is capitals, digits and underscores (400 otherwise). |
| `DELETE /api/projects/{id}/secrets/{name}` | Removes it (404 when not set). |
| `GET /api/secrets`, `PUT`/`DELETE /api/secrets/{name}` | The same for secrets shared with every project. Each row of the list also has `overridden_in` (projects with their own value, which keep it) and `lost_by` (projects that would lose the name if it were removed), by project name. |

A value is never in a response, the journal or a log line (the journal scrubs every secret a team was handed). With no
key every write is a 409 saying how to make one. There are deliberately no secret tools over MCP.

**What an agent's own commands can see.** A name a backend runs on is an agent *credential*; any other name is a project
*secret*, which the project's commands are meant to see. kopicode keeps `OPENROUTER_API_KEY` out of its commands.
Codex is started with `shell_environment_policy.exclude` for `OPENAI_API_KEY`. Claude Code's commands can see
`ANTHROPIC_API_KEY`, and all three can read their own login file as the same OS user; only a sandbox provider, a container
or a separate user stops that. The checks behind this are in `docs/research/harness-credentials-spike.md`.

## Default permissions

A delegation with nothing declared is not shell-less: it gets the built-in presets, and
`--allow` (or a project's own allow list) adds to them. Enabled by default: **inspect**
(`ls`, `cat`, `grep`, `rg`, `find`, ... and the text filters `echo`, `printf`, `tr`, `cut`, `sort`, `uniq`
and `sed -n '<from>,<to>p' FILE`), **git-read** (`git status|diff|log|show`),
**git-save** (`git add`, `git commit -m '<message>'`, `git switch -c`), **python**
(`uv run pytest|ruff|mypy`, `uv sync`), **javascript** (`npm test|ci`, `npm run
test|build|lint|check`, `npx tsc`) and **make** (`make test|check|lint|build|ci`). **go-rust**
and **containers** exist but are off unless declared. So does **git-push**: `git push <remote> <branch>`
for a branch that is not `main`, `master` or the branch `origin/HEAD` names (a per-goal `cuttlefish/<slug>`
branch is fine). A push that names no branch (`git push`, `git push origin HEAD`) still asks.

Never approved, whatever is declared: `sudo`/`su`/`doas`, a forced `git push` (`--force`, `-f`,
`--force-with-lease`, `+branch`, `--mirror`, `--all`, `--delete`), a push of the default branch, a
download piped into a shell (`curl ... | sh`), and arguments that reach outside `--root`.
`find -delete`/`-exec`, `rg --pre` and `git commit --no-verify` are refused too, and so are the forms of a
text filter that write or run something: `sort -o`/`--output`/`--compress-program`/`-T`, `uniq IN OUT`
(its second file is written to), and any `sed` script other than a plain line range.

Matching is by argv prefix on a plain word list. A line that chains commands (`a && b`, `a | b`,
`a; b`, one command a line, `cmd || true`) is read one command at a time and runs when every
command in it is allowed. Quoted arguments are fine (single quotes are literal; double quotes may not
hold `$`, a backtick, `\` or `!`), and so are `2>&1`, `2>/dev/null` and `>/dev/null`. Anything else
(a redirect to a file, `$(...)`, a backtick, a glob, a lone `&`) is not matched and waits for you.
Inside a chain, `git commit` is allowed only as `git commit -m '<message>'`. When a chain waits because
one command is not on the list, **Always allow** offers a rule for the start of the first such command
(for example `git config user.name`), so the same chain runs unasked next time; a launcher (`sh`, `python`, `uv run`, `npx`), including behind a wrapper (`env A=1 uv`), a bare `git`,
`rm`, `mv`, `cp`, `chmod`, `sed` or `awk`, a never-allowed command or a line with a redirect or substitution can still only be allowed once, so no rule is offered for it.
kopicode, Codex (over `codex app-server`) and Claude Code (over stream-json) check this live,
command by command. Over `claude -p` (`CUTTLEFISH_CLAUDE_CODE_TRANSPORT=print`, and inside a sandbox
provider) the list is passed as `--allowedTools` patterns, plus deny patterns for the never-allowed
prefixes. Over `codex exec` (`CUTTLEFISH_CODEX_TRANSPORT=exec`, and inside a sandbox provider) the list only
switches the sandbox to `workspace-write` -- see the known gaps. The `print` transport's exact-match
policy file cannot express prefixes, so it sees the presets but matches almost nothing.

## Built-in roles and teams

`projects add` with no `--role` registers the **builder-reviewer** team. `--template NAME`
picks another (`solo-builder`, `builder-reviewer`, `full-crew`); it cannot be combined with
`--role`. A bare built-in name (`--role reviewer`) is that built-in, and `NAME:PERSONA`
overrides its prompt. The dashboard's `GET /api/roles` and `GET /api/templates` list them,
and `POST /api/projects` accepts `template` the same way.

| Role | Does | Access |
| --- | --- | --- |
| `builder` | Implements in small steps, runs tests, commits, never pushes | standard |
| `reviewer` | Reads the diff and reports findings, most serious first | read-only |
| `tester` | Writes and runs tests, reports bugs rather than patching them | standard |
| `planner` | Turns a goal into an ordered plan | read-only |
| `docs-writer` | Keeps docs true to the code | standard |

A role is "default" while its prompt equals the built-in one; edit it and it is just a role.
**Read-only restricts a role's shell to inspection** (`ls`, `grep`, `git diff`, ...) and
ignores any declared `--allow`. It does not yet stop a role editing files, because that
needs per-backend handling that arrives with the permission modes; until then the
reviewer's and planner's prompts are what keep them from editing.

## Permissions and team, in the dashboard

A project's page has four tabs for working (Overview, Terminal, Needs you, Artifacts) and a **Settings** tab
with three sections: Permissions, Team and Secrets. (A link to an old tab, such as `#/projects/<id>/team`, still opens
that section.) **Permissions** sets the mode, switches command groups on and
off (the same presets as above, plus your own commands) and shows the always-blocked list and
how each agent receives a mode. **Team** edits the project's roles: change a role's prompt,
agent and permissions, add a built-in or custom role, remove one, reset a built-in to its default
prompt, or replace the whole team from a template. The **Role library** destination lists the built-in
roles and teams read-only; each project keeps its own copy of a role, so editing one never
changes the library.

The same settings over HTTP: `GET /api/permissions` (modes, presets, blocked list, backend notes),
`PATCH /api/projects/{id}/mode`, `PATCH /api/projects/{id}/presets` (`{"presets": [...]}`, names
from the catalogue), `PATCH /api/projects/{id}/allow` (your own commands; an entry with shell syntax, a
never-allowed command, or a rule that would run any script (a launcher on its own such as `sh`, `python` or `uv run`, also behind `env A=1`, a bare `git`, `rm`, `mv`, `cp`, `chmod`, or `sed`/`awk`) is refused with a 400
naming the entry and the reason) and
`PATCH /api/projects/{id}/roles` (replaces the whole list; a role may carry `"limits"`). `PATCH /api/projects/{id}/limits` sets the project's own limits (`{"limits": {"max_turns": 40}}`; `null` or an empty object inherits, an unknown key or a value out of range is a 400) and `GET /api/limits` lists the six settings with their range and what each reads now. `GET /api/projects/{id}/environment` is read-only: the ecosystems the project's files point to, each with its package tool, lockfile, the version it asks for and whether its own `.venv` or `node_modules` is there (the dashboard's Environment card), and under `prepare` the install steps a start would run now. The project root and the folders directly under it are read (a `frontend/package.json` counts, a deeper one does not, and a folder of an ecosystem the root already has is treated as a workspace member and skipped); each ecosystem reports its `path`. Nothing is run. `PATCH /api/projects/{id}/environment` with `{"prepare": "ask" | "auto" | "off"}` sets whether cuttlefish installs dependencies before a team starts (below). **Every change applies the next time
the team starts**, not to a team already running.

### Needs-you requests over HTTP

The dashboard shows these under **Needs you**. When a kopicode agent asks to run a command nothing approves, the daemon holds it as a request:

- `GET /api/requests`: every pending request across the fleet, with the project, the role, the
  command, why it stopped, the answers allowed, a suggested "always" rule and `expires_in_s`.
- `GET /api/projects/{id}/requests`: `pending`, then the last 50 that `resolved`.
- `POST /api/projects/{id}/requests/{request_id}/answer` with `{"answer": "allow_once" |
  "allow_always" | "deny", "rule": [...]}` (`rule` only for `allow_always`, and it must be the start
  of the command that was asked). `allow_always` applies to the running team at once and is saved to
  the project's own commands for later starts. The same answer sent twice returns `already: true`;
  a different one, or one after the window closed, is a 409 naming how the request ended; an
  unknown request is a 404; a refused rule is a 422 with the reason and the request stays pending.
- A request of kind `question` is an agent asking a person something (kopicode's `ask` tool, or Claude Code's own
  question). When the agent offered choices the request carries them as `options` (at most six, each cut to 80
  characters; none for a multi-select question) and the dashboard shows each as a button that sends it as the answer.
  It takes `{"answer": "answer", "text": "..."}` (non-empty, at most 4000 characters, passed to
  the agent as the reply) or `{"answer": "decline"}`. The journal and the history keep the text; only
  known secret values are scrubbed from it (as everywhere in the journal), so do not type a
  credential into an answer. It is raised only for a kopicode that
  advertises `ask.request` (v0.4.0 and later) and only where permission requests are, so the
  agent is paused on it exactly as on a command. An older kopicode, `auto`, a read-only role and
  `cuttlefish run` give the agent the fixed "no human is present" reply as before.

An unanswered request denies when its window closes (a question goes unanswered and the agent
carries on), and a restart abandons it.

## Permission modes

`run`, `run-team` and `projects add` take `--mode ask-first|standard|auto` (default
`standard`); a project's mode is also set with `PATCH /api/projects/{id}/mode` and applies the
next time the team starts, not to one already running. A role may override it with its own `access` (`ask-first`, `standard`, `auto`
or `read-only`), and the role's own setting wins.

| Mode | Shell commands | File edits |
| --- | --- | --- |
| `ask-first` | None run on their own. A command stops the agent and appears under **Needs you** for you to allow or deny (on Codex and Claude Code over their live transports, the defaults). | Yes |
| `standard` | The built-in presets plus `--allow`. On kopicode a command off that list appears under **Needs you** instead of being refused; elsewhere it is refused. | Yes |
| `auto` | Any command except the never-allowed list | Yes |
| `read-only` (a role) | Inspection only (`ls`, `grep`, `git diff`, ...) | No on Claude Code and Codex; **yes on kopicode** |

The never-allowed list applies in every mode: `sudo`, a forced `git push`, a download piped into
a shell, and a write outside `--root` (`rm`, `mv`, `cp`, `tee`, `>` and similar aimed at an
absolute, `~`, `..` or variable path). It reads the command line as text, so it is a floor,
not containment.

Only a team started from the dashboard (`cuttlefish serve`) can ask you: `run` and `run-team` have no inbox, so they refuse a command nothing approves.

How each agent receives a mode: kopicode answers every command live, so Auto allows
everything that is not never-allowed and needs `kopicode serve` (not the `print` transport).
Claude Code over stream-json answers every command that prompts itself, like kopicode; in Auto it also
refuses `curl` and `wget`. Over `claude -p` it gets tool allow and deny patterns instead.
Codex over `codex app-server` answers every command's approval itself, so Ask first, Standard and
Auto work as on kopicode, and a read-only role gets the read-only sandbox. A command it accepts runs
outside Codex's own sandbox, so the never-allowed list and the working-directory check are the
only guard. Over `codex exec` Codex only has two sandboxes, so Ask first and read-only both mean
read-only there and Auto is the same as Standard.
