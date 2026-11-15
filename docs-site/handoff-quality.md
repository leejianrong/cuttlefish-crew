# Handoff checks

When a builder's context fills up, the next stretch of work starts from a short document the old
one left behind: a **handoff**. cuttlefish checks that document, and what the builder does next,
with plain string and timing checks. The checks are advice for you. They never block a round, never
ask the agent again, and never change what the next session is told. cuttlefish makes no model call
to produce them.

## Two kinds of handoff

**A round checkpoint.** When a round ends because it ran out of room, cuttlefish asks the agent for a
handoff under four headings (Done, Remaining, Failed or refused, Open questions). Kopicode, Codex over
`app-server` and Claude Code over stream-json can be asked. A one-shot run (`claude -p`, `codex exec`)
or a sandbox cannot take a second prompt, so those get a **digest read from the journal** instead.
The activity log says so when that happens; it does not pretend a question was asked.

**Claude Code's own compaction.** When Claude Code compacts its context itself, all cuttlefish sees is
a "Context was compacted" line in the transcript. There is no document to read, so only what the
builder did afterwards is measured.

## What is checked

For a checkpoint, a **Handoff check** row in the activity log (and `cuttlefish show`) records:

- how many of the four headings the text has, and whether Remaining says anything;
- whether Done names a file the journal shows was edited;
- how many "unconfirmed" markers it has, and whether its length is unusual;
- how many of these facts it mentions: files edited since the last handoff, recent failures, open
  Needs-you requests, and the plan's next step.

For a native compaction, the Overview timeline notes, from the transcript, within a few minutes of
the compaction:

- commands and edits the builder repeated in its first 15 tool calls that it had already run;
- whether it touched the plan file within 30 minutes;
- how long it took to make its next commit or plan edit, against the usual gap between commits;
- nudges or a stuck state within 10 minutes.

A soak run's report lists one row per compaction with these figures.

## How to read it

A mention is a word match, so a handoff can name a file and still be wrong about it, and a good one
can word things in a way the match misses. A repeated `git status` counts as a repeat. Treat a low
number as a reason to look at the handoff yourself, and a high one as nothing more than the absence
of that signal. Nothing is stored for the compaction measures: they are worked out each time you open
the page.
