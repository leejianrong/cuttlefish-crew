# ADR-0004: Memory is four tiers, the MVP builds two, and each tier owns its own store

- Status: Accepted. The handover mechanism (cuttlefish's own model writes the summary) is superseded by ADR-0042; the tiers, the stores and the journal rules stand.
- Date: 2026-08-23
- Deciders: Jian

## Context

"Give the agent memory" is not one feature. Four genuinely different concerns get
called memory, and building them as one undifferentiated system produces something
that does all four badly:

- **Working memory** - what fits in the current context window, and what happens
  when it doesn't anymore.
- **Episodic memory** - a durable, retrievable record of what happened in past
  tasks.
- **Procedural memory** - distilled, reusable knowledge extracted from episodes
  ("how I fixed this build error last time"), which earlier research in this
  suite's planning found is a real, still-underserved gap in the current market:
  most agent frameworks handle it through hand-tuned system prompts rather than
  runtime retrieval.
- **Semantic memory** - general facts about the operator's systems and
  preferences, the kind of thing Zep and Graphiti already build well.

satay's own journal is not automatically any of these. It is optimised for exact,
deterministic replay, comparing two runs call by call, and it is genuinely
excellent raw material. But a record built for "prove this workflow did the same
thing on retry" is not shaped for "find the episode where I last fixed this," and
treating it as episodic memory by default would mean cuttlefish's memory system is
whatever shape satay's replay identity happens to need, rather than the shape
retrieval actually wants.

kopicode already solved the durable-log part of this problem once, deliberately,
after getting it wrong: sibei-flow hand-rolled a transcript as a `list[str]`
appended to as its loop ran, with tool output clipped at 1200 characters. It was
lossy by construction and free to drift, since adding a call and forgetting the
append produced a record of a run that never happened. kopicode's fix - one
session record, typed as a tagged union, never truncated, everything derived from
it - is exactly the shape episodic memory needs, and there is no reason to
rediscover it independently.

There is also a real security gap to close explicitly. satay's own write-time
redaction (ADR-0029 in that repository) protects satay's own journal schema. It
does nothing for a second, cuttlefish-owned store, because it was never meant to.
An agent that shells out to `env`, or reads a file containing a credential, can put
that value into a tool result exactly the way it can in kopicode, and nothing
in satay's own redaction machinery would ever see it happen in a store satay
doesn't write to.

## Decision

**The MVP builds working memory and episodic memory. Procedural and semantic
memory are named and deferred, not designed here.**

**Episodic memory is its own store, `.cuttlefish/episodic.db` (SQLite), never a
table inside satay's own `.satay/` database.** satay owns its schema and its
migration policy; writing into it from outside would be an unversioned change to a
schema this project doesn't own, the same argument satay itself makes for keeping
its own core dependency-free. The event shape mirrors kopicode's journal design
directly: a tagged union of typed events, versioned from the first commit so an
unmarshaller preserves an event type it doesn't recognise rather than dropping it,
and a redactor that strips known secret values at write time, the same discipline
kopicode's journal already proved necessary rather than optional.

**Working memory is a behaviour, not a second store.** When a task's context usage
crosses a threshold, cuttlefish checkpoints the task's state through satay's own
primitives, makes one bounded LLM call over the recent window of episodic events to
distill a summary, discards the raw window from the live context, and keeps a
pointer back into the full episodic record for anything that later needs to
unclip it. The summary itself is written as an episodic event, not a separate
document a human or an agent has to remember to keep updated - the same argument
that already killed the idea of a hand-maintained handover doc in kopicode's own
agent brief, restated one layer up.

**Procedural memory** (a skill-distillation pipeline scanning episodic history for
successful multi-step resolutions, compiling reusable skill files) and
**semantic memory** (a knowledge graph of facts and entities) are both named
explicitly as future tiers and built by nobody in this milestone. Procedural memory
in particular can be prototyped independently of the rest of this project, against
kopicode's own existing session journals, since kopicode already produces the raw
material this tier would consume.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Treat satay's own journal as episodic memory directly. | Couples memory's shape to replay-identity requirements that have nothing to do with retrieval, and writes into a schema this project doesn't own and can't version independently. |
| Build all four tiers now. | The two deferred tiers are real, but unproven and expensive - procedural memory in particular needs real episodic history to distill from before its own design can be trusted, so building it first would mean designing against no data. |
| A hand-maintained handover document for working memory. | This is precisely kopicode's own "lesson worth not relearning": a document nobody is forced to keep in sync with reality goes stale the first time someone forgets to update it. |
| Share one SQLite file between satay's store and the episodic journal, different tables. | Ties this project's schema evolution to satay's own migration cadence and `PRAGMA user_version` scheme for no real benefit, and makes "which schema owns this file" an open question every time either project changes. |

## Consequences

The MVP is honest about what it does: it doesn't lose data, and it doesn't yet get
smarter over time. That is a real, narrower claim than "cuttlefish has memory," and
it is the correct one for a first slice.

Two stores exist side by side (`.satay/` and `.cuttlefish/episodic.db`), which
costs a small amount of operational clarity - a person debugging a stuck task needs
to know to look in both. What it buys is that neither project's schema is
constrained by the other's needs, and that cuttlefish's own redaction discipline is
cuttlefish's own responsibility, checked by cuttlefish's own tests, rather than an
assumption resting on a guarantee satay never made.
