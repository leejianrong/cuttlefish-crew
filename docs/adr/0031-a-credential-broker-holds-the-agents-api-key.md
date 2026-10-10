# ADR-0031: A credential broker holds the agent's API key, so the agent holds a token

- Status: Accepted
- Date: 2026-10-10
- Deciders: Jian (Pandan CUT-E14)

## Context

ADR-0006 injected every credential into the agent's process environment and named a broker as the
real fix, deferred "until the simple version's gaps are felt". They were felt. The spike
(`docs/research/harness-credentials-spike.md`) found that Claude Code's own API key is visible to every
command it runs, and that nothing a harness exposes can hide it without also hiding the project's
secrets. A key in an environment variable is one `printenv` from a prompt-injected command.

The spike also found what a broker can rely on:

- **Claude Code** sends its model calls to `ANTHROPIC_BASE_URL` and puts `ANTHROPIC_API_KEY` in
  `x-api-key`. Verified with a scripted upstream, and with the real binary through the broker.
- **Codex** takes a custom `model_provider` (`-c`) whose `base_url` and `env_key` are ours. It POSTs
  `/responses` and sends the env var as a bearer token. Verified over `app-server` and `exec`.
- **kopicode** already strips its key from its shell (`childEnv`), so the gain is small, and its
  `--provider-url` is not in a released kopicode.

## Decision

**The daemon runs a loopback proxy (`cuttlefish.broker`) and an agent is leased a token instead of
holding its key.** With `CUTTLEFISH_CREDENTIAL_BROKER=1`, `cuttlefish serve` starts the broker with
its first team. A delegation that runs on the host and has a key to lease takes a lease: a random
token, one upstream (Anthropic or OpenAI), a fixed base URL, and the key, which stays in the daemon.
The agent is given a base URL with the token in its path and the token in place of the key. The broker
looks the token up, drops any credential header the agent sent, adds the real key, forwards to the
lease's one upstream, and streams the answer back. The lease is closed when the round ends, whatever the
round does. The real key is also taken out of the secrets handed to the backend.

- **Where it lives.** The broker sits in the daemon process, on `127.0.0.1` and a port the OS chose, not
  on the dashboard's port: a daemon bound to a tailnet must not also serve model calls on that address.
  It rides `Runtime.broker`, never a task argument, so replay and the journal are unchanged (ADR-0006's
  rule that a decrypted value never becomes a task argument still holds).
- **Where the key comes from.** The Secrets store first, then the daemon's environment, and a gateway
  base URL (`ANTHROPIC_BASE_URL`) becomes the lease's upstream, so a gateway user is unaffected. **Codex is
  brokered only for an `OPENAI_API_KEY` set in Secrets**: Codex ignores one in the environment under a
  ChatGPT login, and leasing it would move a subscription user onto a metered key they were not using.
- **Off by default**, and when it is on and cannot start the team does not start: the failure is loud,
  never a quiet return to handing out the key.
- A backend opts in with `BROKER_ROUTE` (`BrokerRoute`); `delegate_to_agent_backend` does the leasing;
  the backend only turns a lease into its own settings (`ANTHROPIC_BASE_URL` and a token for Claude Code,
  the `-c` provider and `CUTTLEFISH_BROKER_TOKEN` for Codex).
- Nothing the broker handles is logged: a line names the upstream, the project, the method and the
  status, never a header, a body, a token or a key.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Keep injecting, document the gap. | What ADR-0006 did; the gap is now measured and a person with a key to protect has nothing to turn on. |
| Claude Code's `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`. | Hides the project's secrets too, needs `bwrap` and `socat`, and failed on this host. |
| A sandbox, container or separate OS user. | The real answer to a readable login file, but a different product decision (ADR-0002); the broker is the part that is cheap and portable. |
| Put the broker on the dashboard's port. | A tailnet-bound daemon would serve model calls to the tailnet, protected only by a token. |
| A token in a header only, not the path. | A harness's base URL is the one thing it lets us set for Claude Code; the path is what routes a lease to its upstream. A header would also need the broker to parse the harness's own auth scheme. |

## Consequences

With the broker on, a command an agent runs finds no real key for Claude Code, and none for Codex when
its key is in Secrets. A leaked lease is worth little: it works only on this machine's loopback, only
until the round ends, and only against its own upstream.

It does not close the rest, and the screens and `known-gaps.md` say so:

- **Login files.** A harness that signs in with a file, not a key (Claude Code's `.credentials.json`,
  Codex's `auth.json`), has nothing to broker, and a command can read the file. Only a sandbox, a
  container or another user closes that.
- **A running agent can use its lease.** It can spend the credit it was already trusted to spend, for the
  length of the round; the broker adds no spending limit of its own (the run's cost ceilings still apply).
- **Other credentials the harness is given.** `CLAUDE_*` is passed through to Claude Code, so a
  `CLAUDE_CODE_OAUTH_TOKEN` in the daemon's environment still reaches its commands.
- **Not covered:** kopicode (already keeps its key from its shell), a delegation inside a sandbox
  provider (it cannot reach the daemon's loopback), and `cuttlefish run` / `run-team` (the daemon only,
  for now).
- A second moving part on the model-call path: a broker bug stops a team. It is off by default for that
  reason, and `cuttlefish doctor` says which way it is set.
