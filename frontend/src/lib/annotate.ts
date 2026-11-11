// A note on a changed diff line, sent back to the builder (CUT-291). Pure, so each rule is
// unit-tested. The note is plain text the agent reads: it names the file, the side and the line
// and quotes the line itself, because by the time the agent reads it the numbers may have moved.
import type { DiffFile, DiffLine, GoalDiffResult } from "./api";

/** The daemon's limit on one message (`chat_input.MAX_MESSAGE_CHARS`). */
export const MAX_NOTE_CHARS = 8000;
/** A quoted line is cut here, so a minified file's one line cannot fill the message. */
const MAX_QUOTE = 240;

export type Side = "new" | "old";

export interface LineRef {
  path: string;
  side: Side;
  line: number;
  /** The line's text, quoted in the note. */
  quote: string;
}

/** Which side of the diff a changed line is on: an added line is the branch's, a removed one the base's. */
export function sideOf(line: Pick<DiffLine, "type">): Side {
  return line.type === "-" ? "old" : "new";
}

/** Whether a line can carry a note: it is a changed line, in a file whose lines are really shown. */
export function canAnnotate(file: DiffFile, line: DiffLine): boolean {
  if (line.type === " " || file.withheld || file.omitted || file.binary) return false;
  return (line.type === "-" ? line.old : line.new) !== undefined;
}

export function lineRef(file: DiffFile, line: DiffLine): LineRef {
  const side = sideOf(line);
  const text = line.text.length > MAX_QUOTE ? `${line.text.slice(0, MAX_QUOTE)}…` : line.text;
  return { path: file.path, side, line: (side === "old" ? line.old : line.new) ?? 0, quote: text };
}

/** The message the builder reads. `diff` says which branch and state the line was seen in. */
export function annotationMessage(
  ref: LineRef,
  note: string,
  diff: Pick<GoalDiffResult, "branch" | "base" | "head" | "dirty">,
): string {
  const where = ref.side === "old" ? `line ${ref.line} before your change (removed)` : `line ${ref.line} (added)`;
  const seen = [
    diff.branch ? `on ${diff.branch}` : "",
    diff.head ? `at ${diff.head.slice(0, 8)}` : "",
    diff.dirty ? "with uncommitted changes" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return [
    `Review note on ${ref.path}, ${where}${seen ? `, as seen ${seen}` : ""}:`,
    `> ${ref.quote}`,
    "",
    note.trim(),
  ].join("\n");
}

/** Whether the branch has changed since the diff was read: a new commit, or different counts. */
export function diffMoved(
  read: Pick<GoalDiffResult, "head" | "files_changed" | "additions" | "deletions">,
  now: Pick<GoalDiffResult, "head" | "files_changed" | "additions" | "deletions">,
): boolean {
  return (
    read.head !== now.head ||
    (read.files_changed ?? 0) !== (now.files_changed ?? 0) ||
    (read.additions ?? 0) !== (now.additions ?? 0) ||
    (read.deletions ?? 0) !== (now.deletions ?? 0)
  );
}

/** What to tell a person who is about to send, or null when it can go. */
export function tooLong(message: string): string | null {
  return message.length > MAX_NOTE_CHARS
    ? `That is ${message.length} characters; a message is at most ${MAX_NOTE_CHARS}. Shorten the note.`
    : null;
}
