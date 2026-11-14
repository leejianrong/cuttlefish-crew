# Exploratory testing with sub-agents

Unit and integration tests check what we thought of. This is for what we didn't: have
sub-agents use the built thing from the outside, on each surface a person or tool uses, and
report what is broken, misleading or ugly. Do it from time to time, and always before
merging a stack of PRs that changes what a person sees (a message, a log line, a screen, a
CLI output, an MCP reply).

The first run (2026-10-07, V5-E0/E1) found three things no test did: an MCP client that gets
only `Error executing tool start_project` while the dashboard shows the reason; log lines
with `project=- team=-` for the daemon's own events; a spawn that bypassed `merge_env` and
leaked cuttlefish's venv.

## Surfaces, one agent each, in parallel

- **Browser:** the dashboard. No browser MCP is connected by default; Playwright works, and
  Chromium builds are cached in `~/.cache/ms-playwright` (install `playwright` in a scratch
  dir, not the repo). Look at screenshots, at phone width and in dark mode, and at the
  console and network errors.
- **CLI:** `cuttlefish doctor`, `serve` (start, Ctrl-C, logs), `make demo`, one-shot `run`.
- **MCP:** `cuttlefish mcp` against a running daemon, with the `mcp` Python client. Ask what a
  *client* sees when something fails, not only what the daemon logs.

## Setup that keeps it safe

1. A detached worktree of the commit under test (`git worktree add --detach <scratch> <sha>`),
   `uv sync` and `npm ci && npm run build` in it. Remove it afterwards.
2. Each agent gets its own scratch `HOME`, its own port (never 8420: a person's live daemon
   may be on it), and its own scratch project (a tiny `git init` repo under that `HOME`).
3. No model credit. Use `tests/unit/delegate/fake_kopicode_serve.py` behind an executable
   wrapper as `CUTTLEFISH_KOPICODE_BIN` (see `test_kopicode_serve.py` for scenarios, for
   example a `max_turns` failure). A live run with real kopicode is a separate, deliberate
   choice that costs cents.
4. Run every drive from a scratch directory outside the repo tree. The CLI reads only `./.env`
   now (CUT-272), but a scratch cwd under the repo is how a real key reached a real kopicode
   once.
5. Stop processes by pid. Never `pkill -f cuttlefish` (it can match a live daemon or the
   shell running the command). Never read `.env` or `secrets.db`; redact tokens and keys in
   reports.

## The brief to give each agent

State what changed and what to verify, as numbered items with the *expected* output, then ask
for **PASS / FAIL / PARTIAL / NOT TRIED per item with evidence** (exact text, exit codes, log
lines, screenshot paths) and a ranked list of anything else that looked broken or confusing.
Say "report what you observe; don't fix code; don't claim it works unless you saw it."
Include the setup rules above verbatim.

## After the report

Triage: fix what the change caused on the branch it belongs to, give UI work its own small PR,
and note anything that was already there. An agent's report is evidence, not a verdict: read
the code it points at before acting on a claim.

## A real Claude Code run from a scratch daemon (2026-10-23)

Done once, with Jian's yes (it uses his subscription): a separate `cuttlefish serve` on its own port with a scratch `HOME`, a scratch
git repo as the project, and `CLAUDE_CONFIG_DIR=$HOME_REAL/.claude` plus `CUTTLEFISH_AGENT_ENV_PASSTHROUGH=CLAUDE_CONFIG_DIR` so
Claude finds its login. Know before repeating it:

- A fresh `HOME` makes Claude show its first-run theme and security screens (each needs one Enter in the tmux session,
  `tmux -S <socket> send-keys -t cf-<project id> ...`). With `CLAUDE_CONFIG_DIR` pointing at the real login they did not appear (2026-11-14).
- The trust-folder dialog's default is "No, exit", so Enter alone leaves Claude. For a goal worktree cuttlefish made, the daemon now
  answers it itself (CUT-182); for any other folder, move to "Yes, I trust this folder" first. The daemon's log says
  `trust dialog for <folder>: answered` or `not seen`. The tmux socket is a hashed path under `/tmp/cuttlefish-<uid>/` when `HOME` is deep.
- Claude records the trust (and projects) in `$CLAUDE_CONFIG_DIR/.claude.json` and a folder per project under `~/.claude/projects`.
  Delete the latter's scratch folders afterwards; the former is Jian's and holds his login, so leave it (it keeps one entry per scratch folder).
- A `~/.claude/settings.json` hook that says `$HOME/.claude/hooks/...` fails under a scratch `HOME` and Claude then refuses every tool
  call ("PreToolUse hook error"). Symlink the real `hooks` folder into `<scratch HOME>/.claude/hooks`.
- `ANTHROPIC_MODEL` is passed through but not honoured: the `model` and `env` entries in the person's `~/.claude/settings.json` win. Use
  `CUTTLEFISH_CLAUDE_CODE_MODEL=haiku` (a `--model` flag, which beats them; checked 2026-11-14, the header read Haiku 5.5). A Write-only task costs a few thousand tokens.
- Keep the task to the Write tool: a Bash call raises a Needs you card, and approving it by script is not allowed.
- To fire a hook by hand use `sessions/hook_script.py --project <id>` with the scratch `HOME`; it reads the token itself.
