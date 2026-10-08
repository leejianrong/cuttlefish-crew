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
Takes effect at the next round boundary, not mid-flight.

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
form takes the same (`name@backend: persona` per role).

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
| `remove` | Deregister a project — never touches its files. |

## `cuttlefish serve`

Start the fleet daemon: launches and owns every registered project's team
concurrently, in one process.

| Flag | What it does |
|---|---|
| `--host HOST` | Loopback by default. A non-loopback host (LAN IP, Tailscale address, `0.0.0.0`) requires `CUTTLEFISH_SERVE_PASSWORD` and switches to password/session auth. |
| `--port PORT` | Auto-picks a free port if the default is taken. |
| `--tailscale` | Bind directly to this machine's own Tailscale IPv4 address — the recommended remote-access path. Overrides `--host`. |
| `--allow-origin ORIGIN` | A browser origin the dashboard may be served from in non-loopback mode. Repeatable. |
| `--browse-root PATH` | A folder the dashboard's folder picker may browse: its subfolders only, with hidden folders and links out of the tree excluded. Repeatable. Default: your home directory. Registering a project is not limited to these. In non-loopback mode, set it to a workspaces folder rather than your home. |
| `--dashboard-dir PATH` | Serve the dashboard's own production build from this directory, same-origin with the JSON API. Default: auto-detect `./frontend/dist`. |

## `cuttlefish doctor`

Checks what is set up and what is quietly wrong, and exits non-zero only for a problem that
stops a start. It reports each backend's binary (location and version), each credential by
name only (set, not set, or still the `.env.example` placeholder; the secrets key is also
checked for being a real Fernet key), `PATH` entries that mislead an agent (cuttlefish's own
venv, Windows `/mnt/...` directories under WSL), the log file, and each registered project
(root exists, an empty `.cuttlefish/secrets.db` left by a failed start, which daemon variables an agent is not given, and what its files say it needs: `Python (uv, wants 3.12, .venv present)`). Values are never printed.

## `cuttlefish mcp`

Run an MCP server (stdio transport) wrapping an already-running
`cuttlefish serve`'s own HTTP API — a separate client process, useful for
driving the fleet from an MCP-aware agent/editor rather than the CLI or
dashboard directly.

```bash
uv run cuttlefish mcp --base-url http://127.0.0.1:8420 --token <token>
```

Sixteen tools. Run and watch: `list_projects`, `get_project`, `register_project` (also takes
`mode` and `template`), `start_project`, `stop_project`, `steer_project`, `approve_project`,
`get_events`. Environment: `get_project_environment` (what the project's files say it needs:
ecosystem, package tool, version, whether its own install is present; nothing is run). Permissions: `get_permissions` (modes, command groups, the never-allowed list and the
per-backend notes), `list_builtin_roles`, `list_templates`, `set_project_mode` and `update_roles`
(which replaces the whole role list, including each role's `access`); both apply the next time the
team starts. Needs you: `list_requests` (the fleet, or one project) and `answer_request`
(`allow_once`, `allow_always` with a `rule`, or `deny`; `answer` with `text`, or `decline`, for a
question), which wrap the
[request routes](#needs-you-requests-over-http) and keep their behaviour: the same answer twice is
fine, a different or late one is a 409, an unknown request a 404 and a refused rule a 422. Answering
lets an agent run a shell command, so it is as weighty as `start_project`.

## Configuration

| Variable | Default | What it does |
|---|---|---|
| `CUTTLEFISH_AGENT_BACKEND` | `kopicode` | Which coding-agent backend a delegation runs through: `kopicode`, `claude-code`, or `codex`. One choice per process — every project and role a given `cuttlefish serve` runs shares it. |
| `CUTTLEFISH_KOPICODE_BIN` / `CUTTLEFISH_CLAUDE_CODE_BIN` / `CUTTLEFISH_CODEX_BIN` | `kopicode` / `claude` / `codex` | Path to that backend's binary, when selected. |
| `CUTTLEFISH_LLM_PROVIDER` | `openrouter` | cuttlefish's own reasoning calls (handover summaries, built only when one is due): `openrouter`, `claude`, or `replay` (keyless, for smoke tests). |
| `CUTTLEFISH_SANDBOX` | `none` | Real containment for the delegation: `none`, `container` (local Docker), or `e2b`. |
| `CUTTLEFISH_AGENT_ENV_PASSTHROUGH` | unset | Names (comma-separated, `*` ends a prefix) to pass to agents on top of the allowlist (see What an agent's environment is). |
| `CUTTLEFISH_KEEP_WINDOWS_PATH` | unset | `1` keeps WSL's `/mnt/...` entries on an agent's `PATH`; they are dropped by default. |
| `CUTTLEFISH_PREPARE_TIMEOUT` | `900` | Seconds one dependency-install step may run before it is killed (see Installing dependencies). |
| `CUTTLEFISH_LOG_LEVEL` | `INFO` | Log level for `cuttlefish serve`: the terminal and `~/.cuttlefish/logs/cuttlefish.log` (rotating, 5 MB x 5). `DEBUG` adds every tool call and permission decision; an unrecognised value falls back to `INFO` and says so. |
| `CUTTLEFISH_STUCK_THRESHOLD` | `5` | How many shell commands in a row may fail on the project's environment (`No module named`, `command not found`, `ENOENT`, `Cannot find module`, ...) before cuttlefish stops a kopicode agent instead of letting it run to `max_turns`. `0` turns it off. Claude Code and Codex are not watched. |
| `CUTTLEFISH_MAX_TURNS` | `100` | Turns a kopicode round may take before it stops (kopicode v0.4.0 or later; an older one keeps its own 20). |
| `CUTTLEFISH_SESSION_TOKEN_BUDGET` | `5000000` | Tokens one kopicode round may spend, counting the history resent on each request; `0` is unbounded. |
| `CUTTLEFISH_MAX_CONTINUATIONS` | `20` | How many times a team role carries on by itself, with a fresh session and the latest handover, after a round stops on turns or tokens and was not stuck. `0` makes that stop a failed round, as before. |
| `CUTTLEFISH_ROUND_TIMEOUT` | `7200` | Seconds one kopicode round may run before cuttlefish cancels it; the role then continues from the handover like any other round that ran out of room. `0` is no limit. A round waiting on you in Needs you counts. |
| `CUTTLEFISH_MAX_IDLE_ROUNDS` | `3` | How many rounds in a row may run out of room (turns, tokens or time) without changing a file before the role is held and a Needs-you card says so. A steer starts the count again. `0` turns it off. |
| `CUTTLEFISH_REQUEST_WINDOW` | `600` | Seconds you have to answer a Needs-you request (a command a kopicode agent wants to run that nothing approves) before it is denied, 10 to 86400. cuttlefish asks kopicode for `--consent-timeout` when `serve --help` lists it (kopicode v0.3.0 and later). An older kopicode denies after its own fixed 60 seconds, so there you get 45. |
| `CUTTLEFISH_SECRETS_KEY` | unset | Enables the project-scoped secrets store. Unset means `cuttlefish secrets`/`--project`/`--secret` are unavailable. |
| `CUTTLEFISH_SERVE_PASSWORD` | unset | Required for any non-loopback `cuttlefish serve` bind. |
| `CUTTLEFISH_MCP_BASE_URL` / `CUTTLEFISH_MCP_TOKEN` | unset | Defaults for `cuttlefish mcp --base-url`/`--token`. |

A real run also needs a model credential for whichever LLM provider and
agent backend are selected: `OPENROUTER_API_KEY` or `ANTHROPIC_API_KEY` for
kopicode and the reasoning provider, Claude Code's own login or
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

## Default permissions

A delegation with nothing declared is not shell-less: it gets the built-in presets, and
`--allow` (or a project's own allow list) adds to them. Enabled by default: **inspect**
(`ls`, `cat`, `grep`, `rg`, `find`, ...), **git-read** (`git status|diff|log|show`),
**git-save** (`git add`, `git commit -m '<message>'`, `git switch -c`), **python**
(`uv run pytest|ruff|mypy`, `uv sync`), **javascript** (`npm test|ci`, `npm run
test|build|lint|check`, `npx tsc`) and **make** (`make test|check|lint|build|ci`). **go-rust**
and **containers** exist but are off unless declared.

Never approved, whatever is declared: `sudo`/`su`/`doas`, a forced `git push`, a
download piped into a shell (`curl ... | sh`), and arguments that reach outside `--root`.
`find -delete`/`-exec`, `rg --pre` and `git commit --no-verify` are refused too.

Matching is by argv prefix on a plain word list, so a chained or quoted command line
(`a && b`, `$(...)`) is denied. The one exception is `git commit -m '<message>'`.
kopicode checks this live; for Claude Code the same list is passed as `--allowedTools`
(plus deny patterns for the never-allowed prefixes) and for Codex it only switches the
sandbox to `workspace-write` -- see the known gaps. The `print` transport's exact-match
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

A project's page has three tabs. **Permissions** sets the mode, switches command groups on and
off (the same presets as above, plus your own commands) and shows the always-blocked list and
how each agent receives a mode. **Team** edits the project's roles: change a role's prompt,
agent and permissions, add a built-in or custom role, remove one, reset a built-in to its default
prompt, or replace the whole team from a template. The **Roles** destination lists the built-in
roles and teams read-only; each project keeps its own copy of a role, so editing one never
changes the library.

The same settings over HTTP: `GET /api/permissions` (modes, presets, blocked list, backend notes),
`PATCH /api/projects/{id}/mode`, `PATCH /api/projects/{id}/presets` (`{"presets": [...]}`, names
from the catalogue), `PATCH /api/projects/{id}/allow` (your own commands; an entry with shell syntax, a
never-allowed command, or a launcher on its own such as `sh` or `python` is refused with a 400
naming the entry and the reason) and
`PATCH /api/projects/{id}/roles` (replaces the whole list). `GET /api/projects/{id}/environment` is read-only: the ecosystems the project's files point to, each with its package tool, lockfile, the version it asks for and whether its own `.venv` or `node_modules` is there (the dashboard's Environment card), and under `prepare` the install steps a start would run now. The project root and the folders directly under it are read (a `frontend/package.json` counts, a deeper one does not, and a folder of an ecosystem the root already has is treated as a workspace member and skipped); each ecosystem reports its `path`. Nothing is run. `PATCH /api/projects/{id}/environment` with `{"prepare": "ask" | "auto" | "off"}` sets whether cuttlefish installs dependencies before a team starts (below). **Every change applies the next time
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
- A request of kind `question` is a kopicode agent asking a person something (its `ask` tool).
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
| `ask-first` | None run on their own. On kopicode a command stops the agent and appears under **Needs you** for you to allow or deny; Claude Code and Codex cannot pause, so there it is refused. | Yes (not on Codex) |
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
Claude Code gets tool allow and deny patterns; in Auto it also refuses `curl` and `wget`.
Codex only has two sandboxes, read-only and workspace-write, so Ask first and read-only both
mean read-only there and Auto is the same as Standard.
