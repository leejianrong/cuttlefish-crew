# The ACP backend

The `acp` backend runs any coding agent that speaks the [Agent Client Protocol](https://agentclientprotocol.com):
JSON-RPC over the agent's stdin and stdout. It is the fourth backend, beside kopicode, Claude Code and Codex. Like the
others it wraps the tool's own surface as it exists and adds no protocol of its own
([ADR-0003](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0003-kopicode-delegation-is-a-wrapped-headless-invocation.md),
[ADR-0005](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0005-agent-backend-becomes-a-pluggable-protocol.md)).

## Set it up

Name the command that starts your agent's ACP entry point, then choose `acp` for a project or one role:

```bash
export CUTTLEFISH_ACP_AGENT="my-acp-agent --acp"
uv run cuttlefish serve
```

`CUTTLEFISH_ACP_AGENT` is split like a shell line: the first word is the program, the rest are its arguments. A team
that names `acp` refuses to start, with a message, if the variable is unset or the program is not on `PATH`; it is
checked at team start, not in the middle of a task. In the dashboard pick **ACP agent** under a project's advanced
settings or a role's **Agent** list. `CUTTLEFISH_AGENT_BACKEND=acp` makes it the daemon's default.

### A different agent for one project or role

`CUTTLEFISH_ACP_AGENT` is the default. To give one project, or one role in it, its own agent, set `CUTTLEFISH_ACP_AGENTS`
to a JSON object. A key is the project's name (or id), and `/<role>` after it names a role:

```bash
export CUTTLEFISH_ACP_AGENTS='{"my-app": "agent-a --acp", "my-app/reviewer": "agent-b --acp"}'
```

A round uses the role's command, else the project's, else `CUTTLEFISH_ACP_AGENT`. Every command named for the project is
checked on `PATH` at team start. It lives in the daemon's environment and not in a file in the project folder on purpose:
an agent that could write its own launch command could run anything. A one-shot `cuttlefish run` has no role, so it uses
the project's command at most.

## What it does

- **One round is one turn.** A task goes in as `session/prompt`; the agent's text, tool calls and usage stream back and
  become the round's summary, edited files, tool calls, tokens and cost.
- **A permission request reaches Needs you.** When the agent asks to run a command (`session/request_permission` with
  kind `execute`), it is decided as it is asked, exactly like kopicode: your command list, then a person under
  **Needs you**, unless the mode is Auto or the role is read-only. The answer goes back as one of the options the agent
  offered.
- **The blocked list holds in every mode,** Auto included (`sudo`, `rm -rf` outside the root, force pushes,
  `curl ... | sh`). It is the same shared list the other backends use. Edits are accepted only inside the project
  folder (a path that climbs out with `..` or a symlink is refused), and never for a read-only role. Anything else the
  agent asks for is refused.
- **Resume.** With `CUTTLEFISH_RESUME=1`, a role's session id is kept in `<root>/.cuttlefish/conversations.json` and the
  next round continues it with `session/load`, when the agent says it supports `loadSession`. A load the agent refuses
  starts a new session. The replayed history is not counted as the answer.
- **A timeout** sends `session/cancel`, then ends the round as failed.

## Know before you use it

**A wrapped agent keeps its own tool's login.** An ACP agent that wraps a signed-in tool, for example the Claude Code
adapter, uses whatever account that tool is signed in to on this machine. In the CUT-232 spike an adapter started with an
empty environment and a scratch `HOME` still reported an authenticated account, so it found the operator's login some
other way. The credential broker ([ADR-0031](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0031-a-credential-broker-holds-the-agents-api-key.md))
cannot hide that: it holds an API key and leases a token, and a login file is not a key. cuttlefish says so where you
choose the backend, and it does not give the agent a model key of its own.

Also, plainly:

- **The command policy is weaker than on the other backends.** The agent writes its own description of a command, and
  only the `title` is guaranteed; your command list matches on that text.
- **No sandbox provider,** and no live steer, checkpoint handoff, client file or terminal methods, or terminal session
  (an ACP agent has no terminal screen to attach to). A project whose own backend is `acp` runs its
  rounds under Advanced; it has no terminal session, so the goal box asks you to pick Claude Code or Codex.
- **An agent that needs to sign in** is asked to `authenticate` with each of its own login methods (the ones that need
  nothing from cuttlefish). An agent that lists methods but is already signed in is simply used. If it still refuses, the
  round fails as **needs login** ("log in to the agent's own tool on this machine, then start again"); cuttlefish holds no
  login for it.
- **Tested against a fake agent.** The suite runs the whole path (a team, a permission asked and answered, a blocked
  command, resume) against `tests/fixtures/fake_acp_agent.py`. Running a real agent costs model credit and was not
  done for this slice; see [the spike](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/research/acp-backend-spike.md)
  for what was learnt from a real adapter without sending it a prompt.
