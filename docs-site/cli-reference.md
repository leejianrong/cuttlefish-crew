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

## `cuttlefish mcp`

Run an MCP server (stdio transport) wrapping an already-running
`cuttlefish serve`'s own HTTP API — a separate client process, useful for
driving the fleet from an MCP-aware agent/editor rather than the CLI or
dashboard directly.

```bash
uv run cuttlefish mcp --base-url http://127.0.0.1:8420 --token <token>
```

Eight tools: `list_projects`, `get_project`, `register_project`,
`start_project`, `stop_project`, `steer_project`, `approve_project`,
`get_events`.

## Configuration

| Variable | Default | What it does |
|---|---|---|
| `CUTTLEFISH_AGENT_BACKEND` | `kopicode` | Which coding-agent backend a delegation runs through: `kopicode`, `claude-code`, or `codex`. One choice per process — every project and role a given `cuttlefish serve` runs shares it. |
| `CUTTLEFISH_KOPICODE_BIN` / `CUTTLEFISH_CLAUDE_CODE_BIN` / `CUTTLEFISH_CODEX_BIN` | `kopicode` / `claude` / `codex` | Path to that backend's binary, when selected. |
| `CUTTLEFISH_LLM_PROVIDER` | `openrouter` | cuttlefish's own reasoning calls (handover summaries, built only when one is due): `openrouter`, `claude`, or `replay` (keyless, for smoke tests). |
| `CUTTLEFISH_SANDBOX` | `none` | Real containment for the delegation: `none`, `container` (local Docker), or `e2b`. |
| `CUTTLEFISH_NEEDS_YOU` | unset | `1` lets a `cuttlefish serve` kopicode team stop and ask a person about a command nothing approves (ADR-0028). Experimental: nothing in the dashboard can answer until a later slice, so leave it unset for now. |
| `CUTTLEFISH_REQUEST_WINDOW` | `600` | Seconds a person has to answer such a request before it is denied (10 to 86400). A kopicode older than the `--consent-timeout` flag allows 45. |
| `CUTTLEFISH_SECRETS_KEY` | unset | Enables the project-scoped secrets store. Unset means `cuttlefish secrets`/`--project`/`--secret` are unavailable. |
| `CUTTLEFISH_SERVE_PASSWORD` | unset | Required for any non-loopback `cuttlefish serve` bind. |
| `CUTTLEFISH_MCP_BASE_URL` / `CUTTLEFISH_MCP_TOKEN` | unset | Defaults for `cuttlefish mcp --base-url`/`--token`. |

A real run also needs a model credential for whichever LLM provider and
agent backend are selected: `OPENROUTER_API_KEY` or `ANTHROPIC_API_KEY` for
kopicode and the reasoning provider, Claude Code's own login or
`ANTHROPIC_API_KEY`, and `codex login` for Codex (`codex exec` ignores an
ambient `OPENAI_API_KEY`, ADR-0018).


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
from the catalogue), `PATCH /api/projects/{id}/allow` (your own commands; an entry with shell syntax or a
never-allowed command is refused with a 400) and
`PATCH /api/projects/{id}/roles` (replaces the whole list). **Every change applies the next time
the team starts**, not to a team already running.

## Permission modes

`run`, `run-team` and `projects add` take `--mode ask-first|standard|auto` (default
`standard`); a project's mode is also set with `PATCH /api/projects/{id}/mode` and applies the
next time the team starts, not to one already running. A role may override it with its own `access` (`ask-first`, `standard`, `auto`
or `read-only`), and the role's own setting wins.

| Mode | Shell commands | File edits |
| --- | --- | --- |
| `ask-first` | None run on their own. **Nothing asks yet**: a command that would need an answer is refused. | Yes (not on Codex) |
| `standard` | The built-in presets plus `--allow` | Yes |
| `auto` | Any command except the never-allowed list | Yes |
| `read-only` (a role) | Inspection only (`ls`, `grep`, `git diff`, ...) | No on Claude Code and Codex; **yes on kopicode** |

The never-allowed list applies in every mode: `sudo`, a forced `git push`, a download piped into
a shell, and a write outside `--root` (`rm`, `mv`, `cp`, `tee`, `>` and similar aimed at an
absolute, `~`, `..` or variable path). It reads the command line as text, so it is a floor,
not containment.

How each agent receives a mode: kopicode answers every command live, so Auto allows
everything that is not never-allowed and needs `kopicode serve` (not the `print` transport).
Claude Code gets tool allow and deny patterns; in Auto it also refuses `curl` and `wget`.
Codex only has two sandboxes, read-only and workspace-write, so Ask first and read-only both
mean read-only there and Auto is the same as Standard.
