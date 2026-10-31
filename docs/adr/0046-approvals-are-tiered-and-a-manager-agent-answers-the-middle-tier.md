# ADR-0046: Approvals are tiered, and a manager agent answers the middle tier

- Status: Accepted
- Date: 2026-10-31
- Deciders: Jian

## Context

Jian wants agents to run for hours or days across 20 to 100 projects, with the person asked only for decisions, and
either most approvals removed or delegated to a manager agent (`docs/plans/2026-10-31-autonomous-portfolio-vision.md`).
Today a request is a person's job: a Needs-you card for a command off the list (ADR-0028), answered from the dashboard.

What already exists, checked in the code:

- **Permission modes** `ask-first`, `standard`, `auto` (`permissions.py`, ADR-0025). Auto lets any shell command run except
  the never-allowed list (`delegate/never_allowed.py`).
- **Requests** are journaled records with a kind (`permission`, `question`, `blocked`) and a request window
  (`CUTTLEFISH_REQUEST_WINDOW`, default 10 minutes) (`requests.py`, `config.py`, ADR-0028).
- **Device tiers** `VIEW`, `APPROVE`, `TERMINAL`, `ADMIN` per route, unlisted routes fail closed (`fleet/tiers.py`, ADR-0039).
- **An MCP server** wrapping the daemon's HTTP API for MCP-native hosts (`mcp.py`, ADR-0020).
- **cuttlefish makes no model call of its own** (ADR-0042). Jian confirmed on 2026-10-31 that this holds: cuttlefish
  provides tools and a stage, and a coding agent does the talking, presenting and managing.

## Decision

### 1. Four tiers decide every request

| Tier | What it covers | Who answers | Model involved |
|---|---|---|---|
| 0. Never allowed | `sudo`, `rm -rf` outside the root, `git push --force`, `curl ... \| sh`, writes outside the root | Nobody; always refused | none |
| 1. Runs unasked | Presets and rules (Standard list, a learned always-allow, Auto mode) | Policy, in the daemon | none |
| 2. Delegated | Requests policy cannot decide | The manager agent, through MCP | the manager's own backend |
| 3. Human decision | A change of direction, cost or risk; anything the manager escalates | The person | none |

Tier 1 is where almost all requests land, so a hundred projects do not mean a hundred model reviews a day.

### 2. The manager is an agent session, not a cuttlefish model call

It is a session on an `AgentBackend` (kopicode on a cheap model, Claude Code, Codex), launched and kept alive by
cuttlefish (resume, agent-written handoff per ADR-0042, journal). It reaches cuttlefish only through MCP tools served at
the **APPROVE** device tier: it can read the attention queue and digests, answer or escalate a request, message an agent
and raise a decision. It cannot reach `ADMIN` routes, so it cannot change policy, modes, secrets, devices or its own tier.
An external MCP client (a person's own Claude Code, say) may use the same tools with the same tier.

### 3. Safety rules

- **Tier 0 is enforced in the daemon**, below the manager. A manager `allow` on a never-allowed command is refused and
  journaled. A test exercises this.
- **A manager never answers a request raised by its own session**, and never one whose project it is not assigned to.
- **Approval input is structured**: command, working directory, role, diff size, the rule that did not match. The agent's
  free-text explanation is shown to the person but is not what the manager is told to trust. A worker's text is
  untrusted input to the manager (prompt injection).
- **Every answer is journaled** with who gave it (policy, manager, person) and the tier of the caller.
- **The request window still applies**: a request the manager does not answer in time follows the existing rule
  (refused on kopicode, ADR-0028), and an unanswered Tier 2 request escalates to Tier 3 first when the window allows.

### 4. Escalation asks a decision, not a command

A new request kind `decision` carries a question, two to four options, a recommended option and the cost of waiting. A
decision may carry a deadline, after which the recommended option is taken and the journal says it was auto-chosen. A
destructive option is never auto-chosen.

## Consequences

- Unattended running is mostly Auto mode plus a branch guard (work on its own branch, never the default branch), not a new
  engine. CUT-182 shrinks to what is missing after that check.
- The manager MCP surface extends `mcp.py` rather than starting over (CUT-231).
- The manager's spend cap reads cost or turns from its backend (CUT-151): dollars on a metered backend, turns on a
  subscription.
- A subscription manager shares quota with the person's own use; the cap must leave room for them.
- This ADR does not build anything. The cards: CUT-182 to CUT-186 (autonomy), CUT-230 (delegated approvals), CUT-231
  (manager tools), CUT-137 (the manager session), CUT-150 (what a digest may carry).
