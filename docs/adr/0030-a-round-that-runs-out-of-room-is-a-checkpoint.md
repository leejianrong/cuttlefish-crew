# ADR-0030: A round that runs out of room is a checkpoint, not a failure

- Status: Accepted
- Date: 2026-10-08
- Deciders: Jian (docs/SLICES.md V5)

## Context

The aim is a team that runs unattended for hours or days. Two facts about the code stood in the way
(checked in `team.py`, `kopicode_serve.py` and `handover.py`):

- **A role ran one round.** A role's delegation finished and the role ended unless a person steered
  it. Nothing started a second round by itself.
- **The cap was kopicode's default and a stop was a failure.** `session.start` carried no limits, so
  kopicode's `serve` defaults applied: 20 turns per prompt and 2,000,000 tokens per session. A
  `max_turns` or `budget_exhausted` stop is a distinct `failure_kind`, so a healthy agent that was
  simply mid-task was journaled as a failed round.

What already existed and is reused: every delegation is its own kopicode session with a fresh
context; `maybe_handover` writes a summary from the journal with cuttlefish's own LLM provider and
`compose_steered_text` feeds it into the next round. So "refresh the context" is the round boundary.
The stuck-agent detector (ADR-0029) catches one pattern only (shell commands failing on the
environment, kopicode only), and the run's cost ceilings are checked at round boundaries.

## Decision

1. **Send limits on `session.start`** when the kopicode binary lists `session.limits` in
   `version --json` (v0.4.0): `max_turns` (`CUTTLEFISH_MAX_TURNS`, default 100, kopicode's own REPL
   default) and `token_budget` (`CUTTLEFISH_SESSION_TOKEN_BUDGET`, default 5,000,000; `0` is
   unbounded). The token budget counts the history resent on every request, so it is sized for a
   long round, not for the context window. An older kopicode keeps its own defaults.
2. **A checkpoint stop continues the role.** When a team round ends with `failure_kind` `max_turns`
   or `budget_exhausted`, was not stuck, and nobody steered or approved it, `run_team` journals
   `RoundContinued` and starts the next round on a fresh session with the latest handover and a line
   saying the previous round stopped for room, not because it was done. At most
   `CUTTLEFISH_MAX_CONTINUATIONS` (default 20, `0` keeps the old behaviour) per role per run; after
   that the last stop fails the role as before, and its error says how many continuations were used.
   `RoundContinued` is journaled before the steering grace, so the role reads `working`, not `blocked`,
   while it waits. A person's steering message still wins, and a
   `require_approval` or cost-ceiling stop still waits for a decision.
3. **Not a checkpoint:** every other failure, including `environment_stuck`, `verification_failed`,
   provider errors and `cancelled`.

## Consequences

- A role can now run `20 x 100` turns unattended by default. The brakes are the stuck detector, the
  run-level `max_tokens` and `max_cost_usd`, and the continuation cap. None of these notices an agent
  that loops without failing shell commands; a no-progress stop (several rounds with no change and
  the same failing verification) and a wall-clock limit per round are the next slices.
- Settings are environment variables for now, not per project or per role.
- Only `run_team` continues. The single-task loop in `workflow.py` (`cuttlefish run`) does not, so a
  turn limit there still fails the task.
- A run resumed from before this change replays its recorded rounds; one that had ended on
  `max_turns` is already terminal and is not reopened.
- Not yet run against a real kopicode, only the scripted fake. A real model's handover quality
  after many continuations is unmeasured.
