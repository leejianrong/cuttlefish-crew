# cuttlefish-crew

A fleet manager for small teams of coding agents, across many software
projects at once. Register your repos, give each project's crew a task in plain
language, and watch every team from one dashboard. Each agent hands the actual
coding to a pluggable backend
([kopicode](https://github.com/leejianrong/kopicode), headless Claude Code, or
headless Codex), and everything that happens lands in one readable journal.

![The Projects screen: two projects, each with working agents, and a Needs you badge](docs/assets/dashboard-projects.png)

When an agent wants to run a command you have not approved, it stops and waits
for you. You answer from the dashboard, and the agent carries on:

![The Needs you tab: a command waiting for Allow once, Always allow or Deny](docs/design/ui-redesign/shipped/v4h-needs-you-light.png)

> **Status**: pre-1.0 and single-operator (one shared password, no per-user
> accounts). [`AGENTS.md`](AGENTS.md) and
> [`agent_docs/known-gaps.md`](agent_docs/known-gaps.md) list what is shipped
> and the gaps that are known and accepted.

```mermaid
flowchart LR
    You(["you"]) <-->|browser| Daemon["cuttlefish serve<br/>one process: API + dashboard"]
    Daemon --> A["project A<br/>builder + reviewer"]
    Daemon --> B["project B<br/>builder"]
    A --> Backend{AgentBackend}
    B --> Backend
    Backend -->|kopicode| K["kopicode serve"]
    Backend -->|claude-code| C["claude stream-json session"]
    Backend -->|codex| X["codex app-server"]
    A -->|every step| Journal[("episodic journal<br/>per project")]
    B -->|every step| Journal
```

The core loop is a durable [satay](https://github.com/leejianrong/satay-runtime)
workflow. If the fleet daemon is killed mid-run, restarting it resumes each
project's in-flight team: same team id, a `TeamResumed` marker in the journal,
no finished round re-run (the round that was mid-flight starts over). A killed
one-shot `cuttlefish run`/`run-team` is not resumed automatically; rerunning it
warns about the unfinished run, and `--resume <id>` with the original arguments
continues it.

## Prerequisites

| You need | For |
|---|---|
| Python 3.12+ and [uv](https://docs.astral.sh/uv/) | everything |
| Node.js and npm | the dashboard (`make demo` builds it once) |
| One coding-agent CLI on `PATH`, logged in or keyed: [`kopicode`](https://github.com/leejianrong/kopicode#readme) (the default; v0.3.0 or later, v0.4.0 for cost, the turn cap, the context limit and questions; needs `OPENROUTER_API_KEY`), `claude`, or `codex` (after `codex login`) | delegating work |
| Docker | only for `CUTTLEFISH_SANDBOX=container` |
| `tmux` | only for the **Agent session** card (an agent that stays up when the browser closes or the daemon restarts) |

Install kopicode with
`curl -fsSL https://raw.githubusercontent.com/leejianrong/kopicode/main/scripts/install.sh | sh`
(it lands in `~/.local/bin`). kopicode reads its key from the environment only,
so `export OPENROUTER_API_KEY=...` in the shell you start cuttlefish from.

cuttlefish makes no model call of its own, so it needs no key of its own: only the coding agent you choose does.
When a round runs out of room the agent writes its own handoff for the next one (or, where it cannot, cuttlefish
writes a digest from the journal), so a long run costs nothing extra.

## Try it: two projects, one dashboard

About ten minutes. Agents spend your model credit, usually cents for the tasks
below.

**1. Install and make two practice repos.**

```bash
git clone https://github.com/leejianrong/cuttlefish-crew.git
cd cuttlefish-crew
uv sync

mkdir -p ~/crew-demo && cd ~/crew-demo
for p in todo-api notes-site; do mkdir $p && git -C $p init -q -b main; printf '.cuttlefish/\n.satay/\n' > $p/.gitignore; done
printf 'class TodoList:\n    def __init__(self):\n        self.items = []\n\n    def add(self, item):\n        self.items.append(item)\n' > todo-api/todo.py
printf '<!doctype html>\n<title>Notes</title>\n<h1>Notes</h1>\n' > notes-site/index.html
for p in todo-api notes-site; do git -C $p add -A && git -C $p -c user.name=demo -c user.email=demo@example.com commit -qm init; done
cd -
```

**2. Start the dashboard.**

```bash
export OPENROUTER_API_KEY=...        # or the key your agent CLI uses
make demo
```

This builds the dashboard once, then runs `cuttlefish serve`: one process
serving the API and the UI. It prints a URL and a token. Open the URL, check
that the address box on the Connect screen matches it (it starts as the address
the page was served from), and paste the token. The token is kept in
`~/.cuttlefish/serve-token` (readable only by you), so restarting the daemon
does not sign an open dashboard out; delete the file to get a new one. No daemon yet? The Connect
screen also links to a sprite gallery that needs no connection. The daemon's
log prints in this terminal and is always written to
`~/.cuttlefish/logs/cuttlefish.log`; `make demo LOG=1` also copies it to
`/tmp/cuttlefish.log`. Ctrl-C stops it cleanly.

**3. Add both projects.** On Projects choose **Add project**, pick
`~/crew-demo/todo-api` in the folder picker, keep the Builder + reviewer team,
and add it. Repeat for `~/crew-demo/notes-site` with the Solo builder team. (From
a terminal, `uv run cuttlefish projects add --name todo-api --root ~/crew-demo/todo-api`
does the same; add `--max-tokens 200000` to cap a project's token use.)

**4. Start both crews.** Open **todo-api**, give the builder a task, and press
**Start team**:

> Add a remove(item) method to TodoList in todo.py, and a test for it. Then run
> `uname -a` and tell me what it prints.

Go back to Projects, open **notes-site**, and start its builder on:

> Add a short About section to index.html.

The two teams run side by side; each agent is a small sprite whose pose is its
status. Roles inside one kopicode project run one at a time, because kopicode
locks a working tree per session.

**5. Answer what needs you.** The bell in the rail and the **Needs you** tab show
a count. A command that is not on the project's list pauses that agent until you
choose **Allow once**, **Always allow** (the start of the command, saved to the
project's commands), or **Deny**. Left alone, it denies itself after ten minutes. With
kopicode v0.4.0 or later, a question the agent asks you shows up here too, with a box for your answer.
You can answer from the project's Overview as well, without switching tabs. A line
that chains commands (`a && b`) is decided command by command, and Always allow
offers a rule for the first command that is not on the list.

How much asks you is the project's permission mode: **Ask first** (every shell
command waits for you), **Standard** (the default: a list of safe commands runs,
others ask) or **Auto** (anything but the never-allowed list, which is `sudo`, a
forced `git push`, a download piped into a shell and writes outside the project
root, in every mode). A role can also be read-only. A mode applies the next time
the team starts; details per backend are in the
[CLI reference](https://leejianrong.github.io/cuttlefish-crew/docs/cli-reference/#permission-modes).

**6. Steer, review, stop.** While a role is running you can send it a
redirect; a Codex role takes it at once, any other role at the next round boundary. Turn on **Approve
each round before it ends** before starting to review a round first. **Stop team**
asks the agent to finish its current step and stop. When it is done, see what the
crew did:

```bash
git -C ~/crew-demo/todo-api diff
```

The Activity log on each project, and `uv run cuttlefish show <team-id>`, replay
every step from the journal.

**7. Keep an agent running.** The **Agent session** card on a project's Overview starts the
project's agent (`claude`, `codex` or `kopicode`, the project's backend) in tmux. Close the browser or
restart `cuttlefish serve` and it is still there; the card says it was **reattached**. After a reboot
tmux is gone, and the next `cuttlefish serve` starts the agent again with the tool's own resume
command (**relaunched**), so the conversation carries on. A Claude Code session asks for permission
under **Needs you** as well as in its own terminal (answer in either); Codex and kopicode ask in the terminal only.
Open the project's **Terminal** tab, which is the real tmux session in
the browser (xterm.js), with a key row for a phone that sits right under the terminal, above the bottom bar. It needs the same
sign-in as the rest of the dashboard, and closing it leaves the agent running. The page title shows the
Needs you count, and if the daemon restarts under an open page it asks you to sign in again.

**8. Read what an agent made.** Agents are told to write a result (a plan, a review, a summary) as a
markdown file under `.cuttlefish/artifacts/` in the project. The project's **Artifacts** tab lists those
files, newest first, and shows markdown and images (png, jpg, gif, webp, svg); it reads the folder again every
few seconds, so a new file appears without a refresh. Other files are listed by name only. Markdown is
rendered by the dashboard itself, with no raw HTML, so nothing an agent writes there runs. Every round's task
carries that instruction, and so does a Claude Code session started in tmux (`--append-system-prompt`). A
Codex or kopicode session in tmux is not told yet, so ask it in your first message.

## Usage from the command line

Run one task against the repo you are standing in (`--root` picks another):

```bash
export CUTTLEFISH_AGENT_BACKEND=codex     # or kopicode (default), claude-code
uv run cuttlefish run "add a .gitignore entry for build artifacts"
uv run cuttlefish show <task-id>          # the id `run` prints
```

State is written to `.cuttlefish/` and `.satay/` in the directory you run from
(both are in this repo's `.gitignore`; add them to yours. A team the dashboard starts
adds them to the project's `.git/info/exclude` itself, so they stay out of its `git status`). Not sure your setup is
right? `uv run cuttlefish init` checks the agent CLI and its login, registers the
repo as a project, and prints the next command.

Let a delegation run named shell commands (default: none; only edits inside
`--root`), or run a crew of named roles:

```bash
uv run cuttlefish run "run the test suite" --allow "python -m pytest"
uv run cuttlefish run-team --role builder:"implement the login form" --role reviewer:"review the last commit"
uv run cuttlefish run --steerable "add a .gitignore entry"     # then: cuttlefish steer <task-id> "..."
uv run cuttlefish run --require-approval "add a .gitignore entry"   # then: cuttlefish approve <task-id>
```

`cuttlefish run` and `run-team` have no inbox: a command nothing approves is
refused there rather than waiting. Only a team started from the dashboard can ask
you. kopicode (`serve`), Claude Code (stream-json) and Codex (`app-server`) can all pause
for you on their default transports. The one-shot transports
(`CUTTLEFISH_CLAUDE_CODE_TRANSPORT=print`, `CUTTLEFISH_CODEX_TRANSPORT=exec`) and any
sandbox provider cannot, so they refuse a command nothing approves.
Everything else (project-scoped secrets, container/E2B sandboxes, token ceilings,
Tailscale access, the MCP server, a preview of the encrypted relay, every flag) is in the
[CLI reference](https://leejianrong.github.io/cuttlefish-crew/docs/cli-reference/).

## Configuration

| Variable | Default | What it does |
|---|---|---|
| `CUTTLEFISH_AGENT_BACKEND` | `kopicode` | `kopicode`, `claude-code`, or `codex`; one choice per process. |
| `CUTTLEFISH_CODEX_MODEL`, `CUTTLEFISH_CODEX_EFFORT` | unset | The model (`--model`) and reasoning effort (`minimal`, `low`, `medium`, `high`) for the Codex backend, so a team can use a small model on your ChatGPT subscription without editing `~/.codex/config.toml`. Unset leaves Codex's own configuration alone. A daemon run as another HOME also needs `CODEX_HOME` pointing at the Codex login, named in `CUTTLEFISH_AGENT_ENV_PASSTHROUGH`. |
| `CUTTLEFISH_CLAUDE_CODE_TRANSPORT`, `CUTTLEFISH_CODEX_TRANSPORT`, `CUTTLEFISH_KOPICODE_TRANSPORT` | `stdio`, `app-server`, `serve` | `print` / `exec` / `print` run the one-shot `claude -p` / `codex exec` / `kopicode run --print` instead of a live session. Those cannot pause for a person, so a command nothing approves is refused. |
| `CUTTLEFISH_KOPICODE_PROVIDER_URL`, `CUTTLEFISH_KOPICODE_MODEL` | unset | Send kopicode's model traffic to an OpenAI-compatible endpoint (a local Ollama at `http://127.0.0.1:11434/v1`, llama.cpp, vLLM, a proxy) instead of OpenRouter, with kopicode's `--provider-url`; the model id the endpoint serves is required with it. Needs a kopicode that lists `provider_url.flag`, works on both the `serve` and `print` transports, and is refused with a sandbox provider (a container cannot reach a host-local endpoint). The endpoint's key, if it wants one, is `KOPICODE_PROVIDER_API_KEY` (optional on loopback); `OPENROUTER_API_KEY` is not passed to kopicode then. kopicode runs these sessions **unpinned** and tuned for no local model, so use it to try the plumbing or a local model, not to compare arms. |
| `CUTTLEFISH_RESUME` | off | `1` keeps one conversation per role (or per one-shot run) for Codex, Claude Code and kopicode, in `<root>/.cuttlefish/conversations.json`, and continues it with the tool's own resume (`thread/resume`, `--resume`, `-resume`), so a later prompt remembers the earlier ones, even after a restart. Only over `app-server`, `stdio` and kopicode's `print` transport (`CUTTLEFISH_KOPICODE_TRANSPORT=print`), not the one-shot transports. The default kopicode `serve` transport continues it with `session.start`'s `resume` when the kopicode lists the `session.resume` feature (`kopicode version --json`; KOP-207), and starts a new session each round on an older one or inside a sandbox provider. A handover checkpoint starts a fresh conversation on purpose. `CUTTLEFISH_CODEX_RESUME=1` still turns it on for Codex alone. Experimental: the session-first path (ADR-0032) will make it the default. |
| `CUTTLEFISH_SANDBOX` | `none` | `none`, `container` (local Docker), or `e2b`. |
| `CUTTLEFISH_REQUEST_WINDOW` | `600` | Seconds you have to answer a Needs-you request before it is denied, 10 to 86400. (For a Claude Code session in tmux nothing is denied when it runs out: the terminal's own dialog still asks.) kopicode v0.3.0 or later honours it; an older kopicode denies after 60 seconds, so there you get 45. |
| `CUTTLEFISH_AGENT_ENV_PASSTHROUGH` | unset | Names (comma-separated, `*` ends a prefix) to pass to agents on top of the allowlist: agents get `HOME`, `LANG`, proxies, toolchain paths and their own credentials, not the daemon's whole environment. `cuttlefish doctor` lists what is withheld. |
| `CUTTLEFISH_CREDENTIAL_BROKER` | unset | `1` makes `cuttlefish serve` keep each agent's model API key itself and lease the agent a short-lived token for a loopback proxy instead, so a command the agent runs finds no real key. Claude Code (a key from the environment or Secrets) and Codex (only an `OPENAI_API_KEY` set in Secrets, because Codex ignores one in the environment under a ChatGPT login); a login file is not a key and is not brokered. A sandbox run is unchanged. |
| `CUTTLEFISH_KEEP_WINDOWS_PATH` | unset | `1` keeps WSL's `/mnt/...` entries on an agent's `PATH` (dropped by default). |
| `CUTTLEFISH_PREPARE_TIMEOUT` | `900` | Seconds one dependency-install step may run before it is killed. |
| `CUTTLEFISH_LOG_LEVEL` | `INFO` | How much `cuttlefish serve` logs, to the terminal and to `~/.cuttlefish/logs/cuttlefish.log` (rotating): `DEBUG` adds every tool call and permission decision. |
| `CUTTLEFISH_STUCK_THRESHOLD` | `5` | How many shell commands in a row may fail on the project's environment (`No module named`, `command not found`, `ENOENT`, `Cannot find module`, ...) before cuttlefish stops a kopicode agent instead of letting it run to `max_turns`. `0` turns it off. Claude Code and Codex are not watched. |
| `CUTTLEFISH_MAX_TURNS` | `100` | Turns a kopicode round may take before it stops (kopicode v0.4.0 or later; an older one keeps its own 20). These are the daemon's defaults: a project, and a role in it, can set its own in the dashboard (Team tab) or over HTTP (`GET /api/limits`, `PATCH /api/projects/{id}/limits`); role over project over this variable over the built-in value. |
| `CUTTLEFISH_SESSION_TOKEN_BUDGET` | `5000000` | Tokens one kopicode round may spend, counting the history resent on each request; `0` is unbounded. |
| `CUTTLEFISH_MAX_CONTINUATIONS` | `20` | How many times a team role carries on by itself, with a fresh session and the latest handover, after a round stops on turns, tokens or time, or after a command was refused, and was not stuck. `0` makes that stop a failed round, as before. |
| `CUTTLEFISH_ROUND_TIMEOUT` | `7200` | Seconds one kopicode round may run before cuttlefish cancels it; the role then continues from the handover like any other round that ran out of room. `0` is no limit. A round waiting on you in Needs you counts. |
| `CUTTLEFISH_MAX_IDLE_ROUNDS` | `3` | How many rounds in a row may end (out of room, or on a refused command) without changing a file before the role is held and a Needs-you card says so. A steer starts the count again. `0` turns it off. |
| `CUTTLEFISH_CONTEXT_LIMIT_PERCENT` | `75` | How full a kopicode round's context may get, as a percentage of the model's window, before cuttlefish ends the round and the role continues from the handover with a fresh context. Needs kopicode v0.4.0 and a model whose window kopicode knows; otherwise nothing happens. `0` turns it off; above `95` counts as `95`. |
| `CUTTLEFISH_SECRETS_KEY` | unset | Turns on the encrypted, project-scoped secrets store. Must be a Fernet key. |

Binary paths, serve/MCP settings and the rest are in the
[configuration table](https://leejianrong.github.io/cuttlefish-crew/docs/cli-reference/#configuration).
A `Dockerfile` and `fly.toml.example` exist for Fly.io hosting; they have been
built and run locally but never deployed (ADR-0015).

Known limits worth knowing before you spend money: the dashboard shows dollars
for a backend that reports a cost (kopicode since v0.4.0, Claude Code) and
"cost unknown" for one that does not (Codex), so a dollar limit stops the first
two and only a token limit stops Codex. Cost is never estimated. **Stop** takes
effect when the current round ends, which can be minutes. Live tests spend real
credit: `make test-live` asks first, and one 100-turn kopicode run cost about
$0.71 (`agent_docs/model-costs.md`).

## Documentation

[Docs site](https://leejianrong.github.io/cuttlefish-crew/docs/) (quickstart,
CLI reference, architecture) · [`docs/PLAN.md`](docs/PLAN.md) (where this is
headed) · [`docs/adr/`](docs/adr/) (why each decision was made) ·
[`docs/research/`](docs/research/) (competitor comparison).

## Contributing

There is no `CONTRIBUTING.md`; [`AGENTS.md`](AGENTS.md) is the contributor guide
(toolchain, branch and PR workflow, and the architectural boundaries). `main` is
PR-only and `make ci` must pass. Issues and questions:
[GitHub issues](https://github.com/leejianrong/cuttlefish-crew/issues).

## The rest of the suite

- [kopicode](https://github.com/leejianrong/kopicode): the reference coding
  specialist this project delegates to.
- [satay-runtime](https://github.com/leejianrong/satay-runtime): the durable
  runtime this project's core loop is built on.
- [sibei-flow](https://github.com/leejianrong/sibei-flow): auto-heals broken
  data pipelines; its hand-rolled transcript is the lesson this project's
  journal is built to avoid repeating.
- [tingkat](https://github.com/leejianrong/tingkat): a multi-LoRA routing
  benchmark.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
