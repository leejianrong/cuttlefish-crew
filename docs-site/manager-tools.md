---
icon: lucide/user-cog
---

# Let a manager agent watch your projects over MCP

A manager is an agent you run yourself (Claude Code, for example) that watches many projects through a small set of
MCP tools: read what is waiting, answer a permission once, message an agent, or ask you to decide. It is held to a
short route list, so it cannot change settings, secrets or devices. This page connects one. The full tool table is in the
[CLI reference](cli-reference.md#the-managers-tools).

## Connect it

Start the daemon (`uv run cuttlefish serve`) and note its URL. The manager server needs no token on the command line:
it reads the manager credential from `~/.cuttlefish/manager-token`, which the daemon creates (mode 0600). Add it to
the agent's MCP config, for example a `.mcp.json`:

```json
{
  "mcpServers": {
    "cuttlefish-manager": {
      "command": "uv",
      "args": ["run", "cuttlefish", "mcp", "--manager", "--base-url", "http://127.0.0.1:8420"]
    }
  }
}
```

`--token` is ignored with `--manager`, so a manager is never started with your owner token by mistake. Delete the
token file to rotate it.

## What it can do

| Tool | Use it to |
|---|---|
| `list_attention` | See every pending request across projects, oldest first. |
| `project_digest` | Read one project's status, last events, blockers, pending requests and spend. |
| `recent_answers` | See how a project's last requests ended. |
| `answer_attention` | Allow once or deny a permission. Never an always-allow rule. |
| `message_agent` | `project_id`, `text`, optional `role`. With a running team, pass the `role` to steer; a project whose builder is an agent session has no roles, so leave `role` out and the text is typed into its chat. Says so when neither exists. |
| `raise_decision` | Ask you to choose between two to four options, with a recommendation. It waits with no timer. |

Every answer is journaled with who gave it and the tier, so the timeline shows `manager` apart from you. A command on the
never-allowed list (`sudo`, a forced push, a download piped into a shell) is denied whatever the manager answers.

When the manager runs as a Claude Code session it is launched with `--strict-mcp-config`, so it sees only the
`cuttlefish-manager` server and none of the connectors in your own Claude account. Its tools stay pre-approved.

Deleting a project (`DELETE /api/projects/{id}`) removes its registry row only. It does not stop that project's tmux
session; stop the agent first from the project page (CUT-271 tracks making delete do it).

## What it cannot do

The daemon holds the credential to the Approve tier and to eight routes. Anything else, such as policy, modes, secrets,
devices, the terminal, or starting and stopping a team, is a 403. It cannot pick an option on a decision. Setting
`CUTTLEFISH_MANAGER_PROJECT` to a project id stops the credential answering or steering in that project, which is meant
for the project that hosts the manager's own session.

## Delegating approvals

Set `CUTTLEFISH_DELEGATE_APPROVALS=1` on the daemon (off by default) and a permission request raised in any project wakes the
manager: the daemon types one message into its session (Claude Code only), batching a burst and spacing messages 30
seconds apart. The manager reads the digests and allows once or denies what is routine. The request stays in Needs you the
whole time, so one it cannot decide, or a manager that is not running, reaches you as before; `raise_decision` asks you
outright. The never-allowed list still holds and every answer is journaled as `manager`. See
[ADR-0053](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0053-delegated-approvals-wake-the-manager-and-leave-the-card-open.md).

The setting can also be made per project, on the project's Permissions tab under "Manager approvals": On or Off
overrides the daemon's setting for that project, and "Follow the daemon" (the default) uses it. Over the API it is
`PATCH /api/projects/<id>/delegate-approvals` with `{"delegate_approvals": true|false|null}` (Admin tier). Each change is
journaled in the project's session and shows in its timeline.

## Waking the manager on a schedule

The manager's own project takes a [schedule](schedules.md) like any other, but its text is a check-in rather than a goal:
when one is due the daemon types it into the manager's session (Claude Code only, the same send path as the approval
wake) and the manager looks over your projects. A run is skipped, journaled and shown on the project page when the
manager is busy, waiting at a permission dialog, not running, or on Codex, so a check-in never interrupts work or lands
as the answer to a dialog. Add one with `cuttlefish schedule add <manager-project-id> --goal "Check in on every project" --recurrence "daily 07:00"`.

Treat a request's text as the worker agent's words: the tools mark it untrusted, and the manager's instructions say to
read it as data.

## The digest is safe by construction

`project_digest` is derived from the journal and the open requests. It carries no code, file contents, command output or
secrets, because each line comes from a fixed list of typed fields rather than from stripping text afterwards. The only
free text is a request title, the goal, Autopilot's reason and a pull request URL, each cut to one line of 120
characters. It is also at `GET /api/projects/<id>/digest`. The schema is in
[`docs/digest.md`](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/digest.md). `cost_usd` is `null` when no
backend reported one, which is not the same as zero.

## See also

- Why: [ADR-0046](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0046-approvals-are-tiered-and-a-manager-agent-answers-the-middle-tier.md)
  and [ADR-0050](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0050-the-manager-credential-is-its-own-token-held-to-the-approve-tier-and-a-route-list.md).
