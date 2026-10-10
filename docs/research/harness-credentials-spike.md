# Spike: what each harness can hide from its own shell

Date 2026-10-10. Pandan CUT-78. Harnesses: kopicode v0.4.0 (and `main` for `--provider-url`), Claude Code
2.1.296, Codex 0.155.1.

## The question

cuttlefish hands a harness two kinds of credential through one path (`_credential_envs`, ADR-0006): the
harness's **own** key (what powers it) and a project's **secrets** (what the project's code needs). Both
end up in the harness process's environment. An agent with a shell can run `printenv`. Can each harness
keep its own key out of the commands the agent runs, and can it still hand the project's secrets to them?

## Method

Nothing real was used. Each run started under `env -i` with only canary values (`CANARY_*`), `PATH` and
`HOME`, so no real secret could reach any output. The agent was made to run `printenv` (or read a file) and
the command's own output was read back from the harness's record.

- **kopicode:** built from `main` and pointed at a fake OpenAI-compatible endpoint (`--provider-url`) that
  scripts one `run_shell` call. No model, no key.
- **Claude Code:** `ANTHROPIC_BASE_URL` pointed at a fake Messages endpoint that scripts one `Bash` call.
  Scrub mode needs `bwrap` and `socat`; it failed on this WSL host (see below), so those runs were inside
  an Ubuntu 22.04 container as root.
- **Codex:** a real `codex exec` with the existing login, one `printenv` command, under the clean
  environment. Only canary variables were in the environment the command could see.

The harness scripts are not kept; the method above reproduces them.

## Results

| | Key in env visible to a command | Project secret in env visible | Login file readable by a command | A setting that hides the key |
|---|---|---|---|---|
| **kopicode** | **No** for `OPENROUTER_API_KEY` and `KOPICODE_PROVIDER_API_KEY`: `childEnv` strips them. **Yes** for `ANTHROPIC_API_KEY` (kopicode never reads it, cuttlefish still forwards it) | Yes | Nothing to read: the key is env-only | Built in, always on |
| **Claude Code** | **Yes** by default (`ANTHROPIC_API_KEY`) | Yes | Yes (`.credentials.json` in the config dir is mode 600, owned by the same user; a `python3` command read it with and without scrub mode; Claude's own `cat` check refuses paths outside the project but only covers `cat`) | `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`: hides the key. It also hides a project secret, whether given in the process environment or in `settings.json` `env`. Needs `bwrap` and `socat` |
| **Codex** | **Yes** by default (`OPENAI_API_KEY`; Codex ignores it for a ChatGPT login, but a command still sees it) | Yes | Yes, even in the `workspace-write` sandbox (`auth.json`, mode 600) | `shell_environment_policy.exclude=["NAME", ...]` hides exactly the named variables and keeps the rest. `inherit="core"` hides everything; `set={...}` adds chosen variables back |

Other Codex findings: `ignore_default_excludes=false` made no difference in 0.155.1 (the KEY/TOKEN name
patterns were not applied), so do not rely on it.

### Claude Code scrub mode on this host

Scrub mode failed closed. With `socat` missing it refused to run any command. With `socat` present on this
WSL host it failed with `bwrap: Can't mkdir /mnt/c/Program Files/ClaudeCode`, a Windows policy path. In a
container it worked as root and failed as a non-root user (`Can't mkdir /mnt/c`). It is not something to
switch on for every user, and on macOS there is no `bwrap`.

## What this means for cuttlefish

1. **Only kopicode keeps its own key from its shell today**, and only for the OpenRouter variables.
2. **A key and a login file cannot be hidden from a shell by an environment setting alone.** A login file is
   readable by the same OS user whatever the harness does. Only a different user, a container or a sandbox
   provider stops that, or a broker that keeps the key out of the harness entirely (ADR-0006 deferred it).
3. **Codex can do the split cleanly.** Pass `shell_environment_policy.exclude` with the backend's credential
   names (names only, never values, so nothing secret goes in argv). Project secrets stay visible, as
   intended.
4. **Claude Code cannot do the split cleanly.** Scrub mode hides everything, project secrets included, and is
   not portable. Until a broker exists the honest statement is that its commands can see its key.
5. **cuttlefish forwards `ANTHROPIC_API_KEY` to kopicode**, which never reads it. That exposes the key to
   the agent's shell for no benefit. Forward only what each harness reads.

## Decisions taken from this (slice 1, CUT-79)

- Split the two kinds in the model: a name in a backend's `CREDENTIAL_ENV_VARS` is an **agent credential**;
  any other name is a **project secret**.
- kopicode: forward only `OPENROUTER_API_KEY` (and `KOPICODE_PROVIDER_API_KEY` when set).
- Codex: pass `shell_environment_policy.exclude` for its credential names. Not yet verified over
  `codex app-server`, the default transport; check before relying on it there.
- Claude Code: no enforcement. The Permissions tab and the Secrets screen say its commands can see its key.
- Not decided here: a credential broker.
