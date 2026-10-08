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

## Update: the guards

- **A wall-clock limit per round** (`CUTTLEFISH_ROUND_TIMEOUT`, default 2 hours, `0` off). kopicode's
  `serve` has no time limit, so cuttlefish cancels the session itself. A timeout used to raise
  `DelegationError`; it is now a failed outcome with `failure_kind="round_timeout"`, keeping the edits and
  tool calls the round made, and it is a checkpoint stop like the other two. A round held open for a
  person's answer counts against the clock.
- **A no-progress stop** (`CUTTLEFISH_MAX_IDLE_ROUNDS`, default 3, `0` off). The signal is the round's
  `edited_paths` (kopicode's `edit_applied` events and confirmed whole-file writes): after N checkpoint
  rounds in a row with none, the role is not continued. A blocked Needs-you card ("has gone round in
  circles") says so, the role waits for a steer with no timeout (as for a stuck agent), and a steer starts
  the count again. Without a steerable team the role ends failed and its error says it was held. Reads and
  shell commands that change files only through the shell do not count as edits, so a role that works by
  running a generator can look idle; raise the setting or turn it off there.
- The hold card is raised by a durable task (`raise_no_progress_card`), not from the workflow body: a
  workflow is replayed, and a card raised in the body came back on every replay after a steer (found by
  the exploratory pass). The role's card on the project page says it waits for a steer, with no timeout,
  while such a card is open.
- **A restart keeps a Stuck card.** `blocked` requests (ADR-0029's stuck agent, and this ADR's held role)
  have nothing waiting on them, so the startup sweep leaves them open for a team it is about to resume
  (`sweep_abandoned(keep_blocked=...)`), and `_launch_team` puts them back in the broker with
  `restore_blocked`, without a second `RequestRaised`. The resumed role replays to the same wait for a
  steer, and the card, badge and role-card text say so. A team that is not resumed still abandons them.


## Update: the first live run, and what a handover must hold

First run of the mechanism against real kopicode v0.4.0 and a real model (2026-10-08,
`tests/integration/test_context_refresh_live.py`, a few cents): kopicode accepted `max_turns` and
`token_budget` on `session.start`, stopped on `max_turns`, and cuttlefish opened a new kopicode session
for every round (5 sessions, 5 handovers in the first run). It also found two things.

- **The handover was nearly empty.** The summariser was given the task text and `stop=max_turns`, because
  the journal text it reads ignored `ToolCallRecorded`, a failed round's `detail` and a completed round's
  `edited_paths`. The first handover said the results "aren't captured" although the round had written a
  file. `handover._texts` now includes each tool call (tool, status, detail), a refused command, a failed
  round's detail and the files a completed round edited, and the prompt asks for files created or changed,
  what is done, what remains and what failed. A continued round's per-round summary also lists the files it
  changed. The same task now produces handovers that name the file written, what is left and the next step.
  This also raises the journal's token estimate, so handovers fire somewhat sooner than before.
- **A refused command ends a role.** In that first run the agent tried a chained `cat ... && echo` outside
  the allowed commands after all four files existed. A refused round is not a checkpoint, so the role ended
  with `denied`. Inside the daemon a command off the list becomes a Needs-you request instead, so this
  only bites runs without an inbox (`cuttlefish run-team`, the tests).
- **A model may finish early.** In one of the two later runs the model called the task finished with two
  of four files. That is the model's call, not a cuttlefish fault, so the live test asserts the mechanism
  and that progress crossed sessions, and only reports the final file count.

## Update: the first manual run (real kopicode, a project with its own `.venv`)

A real kopicode v0.4.0 team (one builder, `CUTTLEFISH_MAX_TURNS=6` then 12, a few cents) on a small Python
project whose `.venv` held a package cuttlefish's own did not. The agent's `pytest` ran under the project's
interpreter and imported it, so the environment slices (E3 to E5) held with a real model. It found two bugs:

- **A refused round ended the role.** One denied command (a hallucinated `cd /testbed && git status`)
  ended a round as `refused`, and the role failed on the spot although the agent had carried on for five
  more tool calls. For a run meant to last days that is the wrong default. A refused round is now a
  checkpoint like the others (`_checkpoint_reason`): the next round is told which command was refused and
  to do the work another way, and a loop of refusals ends at the no-progress hold. It also drops "git status"
  as a prescribed first step in favour of "list and read the files, git status, the tests", and says the
  agent is already in the repository root. In the second run the agent, refused a chained
  `git add ... && git commit`, ran the two as plain commands in a later round and committed.
- **Files written with real content were not counted as edits.** kopicode's stream cuts each tool call's
  arguments at 120 characters, so a `write_file` with content has no usable `path` there, and cuttlefish saw
  no edit. That made the no-progress guard fire on a role that was working, and made a round with a refusal
  and real writes read as `refused`. The session record holds the call in full (`ToolCallParsed` `args`), so
  `SessionRecord.written_paths` reads the paths of writes and deletes the tool did not fail on, and they join
  the edits the classifier sees. A path seen twice counts once.

Also seen: the real model asked a question ("should I commit?") through `ask`, which arrived as a live
question card, and the person's decline reached it as "no human is present". The run was driven through the
HTTP API, not by clicking the dashboard.
