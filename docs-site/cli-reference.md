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
| `--allow CMD` | One shell command the delegation may run inside `--root`, shell-quoted. Repeatable. Default: none. |
| `--token-budget N` | Working-memory handover threshold, in estimated tokens. |
| `--project NAME` | This task's secrets scope. Default: `--root`'s own directory name. |
| `--secret NAME` | One named secret this task's backend may read (needs `CUTTLEFISH_SECRETS_KEY`). Repeatable. |
| `--steerable` | Open a local control API so `cuttlefish steer` can redirect this run. |
| `--require-approval` | A round never finalizes on its own — blocks until `cuttlefish approve` decides it. Implies `--steerable`'s control API. |
| `--max-tokens N` / `--max-cost-usd USD` | Force a decision once cumulative usage crosses this ceiling — the same review gate `--require-approval` uses. |

## `cuttlefish run-team`

Run several named roles concurrently against one project.

```bash
uv run cuttlefish run-team \
  --role builder:"implement the login form" \
  --role reviewer:"review the last commit for style issues"
```

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

## `cuttlefish projects`

Manage the `Project` registry the fleet daemon reads from
(`~/.cuttlefish/projects.db`).

```bash
uv run cuttlefish projects add \
  --name demo --root /path/to/checkout \
  --role builder:"careful, writes tests first" \
  --allow "go test" --max-tokens 200000
```

| Subcommand | What it does |
|---|---|
| `add` | Register a project — `--name`/`--root` required; `--secrets-scope`, `--role NAME[:PERSONA]` (repeatable), `--allow CMD` (repeatable), `--max-tokens`/`--max-cost-usd` optional. |
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
| `CUTTLEFISH_LLM_PROVIDER` | `openrouter` | cuttlefish's own reasoning calls (handover summaries): `openrouter`, `claude`, or `replay` (keyless, for smoke tests). |
| `CUTTLEFISH_SANDBOX` | `none` | Real containment for the delegation: `none`, `container` (local Docker), or `e2b`. |
| `CUTTLEFISH_SECRETS_KEY` | unset | Enables the project-scoped secrets store. Unset means `cuttlefish secrets`/`--project`/`--secret` are unavailable. |
| `CUTTLEFISH_SERVE_PASSWORD` | unset | Required for any non-loopback `cuttlefish serve` bind. |
| `CUTTLEFISH_MCP_BASE_URL` / `CUTTLEFISH_MCP_TOKEN` | unset | Defaults for `cuttlefish mcp --base-url`/`--token`. |

A real run also needs a model credential for whichever LLM provider and
agent backend are selected: `OPENROUTER_API_KEY` or `ANTHROPIC_API_KEY` for
kopicode and the reasoning provider, Claude Code's own login or
`ANTHROPIC_API_KEY`, and `codex login` for Codex (`codex exec` ignores an
ambient `OPENAI_API_KEY`, ADR-0018).
