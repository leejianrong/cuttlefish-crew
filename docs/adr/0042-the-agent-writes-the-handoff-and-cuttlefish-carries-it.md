# ADR-0042: The agent writes the handoff, and cuttlefish carries it

- Status: Accepted
- Date: 2026-10-18
- Deciders: Jian

## Context

A round that runs out of room (turn cap, token budget, a full context) ends, and the next session starts with
nothing but what it is told (ADR-0030). Until now what it was told came from cuttlefish's own model, which wrote a
summary from the journal (`handover.py`, ADR-0004). The journal lists which tools ran but not what they printed or why
a choice was made, so that summary is written by something that did not see the work.

kopicode already has the right primitive. `session.handoff` (kopicode ADR-0026) is one model call with no tools,
made from the session's own history, plus a facts block from kopicode's journal (files written, the last verification,
usage). It does not end the session, and it leaves "when to hand off" to the caller. cuttlefish never called it.

Jian decided on 2026-10-18 to retire cuttlefish-written handovers in general: the coding agent has all the context, so
it writes the handoff, and no real-task comparison is needed first.

## Decision

1. **At a checkpoint, cuttlefish asks the agent.** When a kopicode round stops for room (`max_turns`,
   `budget_exhausted`, or a context cuttlefish ended as nearly full), and before the session is closed,
   `run_kopicode_serve` sends `session.handoff` with a goal naming the work. The document comes back as the outcome's
   `handoff`. A finished round, a failure and a cancel for any other reason are not asked.
2. **The document is the handover.** `maybe_handover(..., agent_document=...)` journals it as `HandoverWritten`
   unchanged, with no summarising call, so the next round reads it through `latest_handover_summary` as before. The
   repository's git state is still appended. Secrets are redacted when the event is written, as for every event.
3. **On by default.** `CUTTLEFISH_HANDOFF=summary` keeps the old summary for a run where the agent's cannot be had. It
   goes when the summariser does.
4. **If the agent cannot write one, nothing is lost:** the call times out (240 s), errors, or answers without a
   document, the outcome carries none, and the round continues from cuttlefish's old summary. That path needs the
   summariser's key and is the part still to retire.
5. **Not built here:** Claude Code and Codex have no such method, so their handoff is a prompt in the live session,
   and the fallback becomes a digest that needs no model (CUT-140). After that the summariser code is deleted.

This supersedes the part of ADR-0004 where cuttlefish's own model writes the handover. The tiers, the stores and the
journal rules stand: the handoff is a journal event, the journal stays the record, and nothing is dropped from it.

## What cuttlefish's model is for

Not handovers. Its job is a manager's: consolidating progress across projects and reporting to the person
(CUT-137). The handoff is the one job that needs the context only the agent has.

## Consequences

- One more model call per checkpoint, made by the agent's own provider and counted against its session budget. A
  budget already spent can refuse it, which is the fallback case above.
- kopicode starts the next session with verification reset, so a handoff that says tests pass is a claim, not a result.
- kopicode's draft must have its named sections or it answers an error; the error is the fallback case, not a failure
  of the round.
- A continuation no longer needs a summariser key when the agent's handoff is had.

## Checked

- Unit tests with the scripted fake: asked at the turn cap and at a full context, asked while the session is still open,
  not asked for a finished round or without a goal, a failed or blank draft leaves the outcome as it was, and a team
  tells the next round what the agent wrote with no summarising call.
- The real kopicode (built from source, v0.5.1-dev, advertising `session.handoff`) against a fake model server: the
  round stopped at the turn cap, cuttlefish asked, kopicode returned its document and wrote
  `.kopicode/handoff/<session>.md`; with a reply missing the sections it answered an error and cuttlefish fell back;
  with no goal it asked nothing.
- **Not checked:** quality of a handoff from a real model on a real task. Jian chose not to compare first. The soak
  harness (CUT-136) is where that shows up.

## Update 2026-10-22 (CUT-140)

- **Codex** now writes its own handoff: when a round ends because it used its round token limit or its round time limit, the
  daemon starts a second turn on the same thread asking for it (fixed headings, commands and edits declined) and keeps the reply
  if it has the Done and Remaining headings. Checked against the real `codex app-server` (codex-cli 0.155.1).
- **Claude Code** (live stream-json transport) is asked the same way, as another user message on the same process, when a round ends
  on its time limit, the one limit it has. Written against the scripted fake, then run once against a real `claude` by another agent before merge (Jian, 2026-10-22); that run's result was not recorded in the repo (CUT-140 part 2).
- **The fallback is a digest, not a summary.** `handover._digest` reads the journal (files changed, the agent's last words, failures
  and refusals, what the person said) and says it is not a report the agent wrote. It covers the whole history so it is complete
  without nesting the digest before it, and an agent-written earlier handoff is carried, clipped.
- **The summariser is deleted:** `cuttlefish.llm`, `tasks.llm.call_llm`, `Runtime.llm_provider`, `CUTTLEFISH_LLM_PROVIDER`,
  `CUTTLEFISH_LLM_MODEL`, `CUTTLEFISH_HANDOFF=summary` and the `anthropic` and `openai` dependencies. A continuation needs no key.
  `LlmCallCompleted` and `LlmCallFailed` stay as event types so journals written before this still decode.
