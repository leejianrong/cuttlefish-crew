// Plain-language labels for the activity log. The journal's event names and rule ids are the
// daemon's vocabulary; the person reading the log should not need them.

import type { EpisodicEventView, RequestResolution } from "./api";
import { OUTCOMES } from "./requests";

const LABELS: Record<string, string> = {
  TaskSubmitted: "Task",
  DelegationStarted: "Round started",
  DelegationCompleted: "Round done",
  DelegationRefused: "Refused",
  DelegationFailed: "Round failed",
  SteeringMessage: "You",
  SteeringQueued: "You",
  HandoverWritten: "Checkpoint",
  LlmCallCompleted: "Summary",
  TaskCompleted: "Finished",
  TaskFailed: "Task failed",
  TeamResumed: "Resumed",
  RoundContinued: "Continued",
  TeamBranchMade: "Team branch",
  RoleMergeReported: "Merge",
  StopRequested: "Stop asked",
  TeamStopped: "Stopped",
  EnvironmentPrepareStarted: "Installing",
  EnvironmentPrepared: "Install done",
  ToolCallRecorded: "Tool",
  ConsentDecided: "Command",
  RequestRaised: "Needs you",
  RequestResolved: "Answered",
};

export function eventLabel(type: string): string {
  return LABELS[type] ?? type;
}

/** The label for one event: an install that failed must not read "done". */
export function eventLabelFor(event: EpisodicEventView): string {
  if (event.event_type === "EnvironmentPrepared" && event.payload.ok === false) {
    return "Install failed";
  }
  return eventLabel(event.event_type);
}

/** The seqs of rounds that ran out of room (turns, tokens, time) and were continued: not failures,
 * the next round for that role began from the handover. Read from the journal, not stored. */
export function checkpointedRounds(events: readonly EpisodicEventView[]): Set<number> {
  const found = new Set<number>();
  events.forEach((event, index) => {
    const kind = event.payload.failure_kind;
    if (
      event.event_type !== "DelegationFailed" ||
      typeof kind !== "string" ||
      kind === "refused" ||
      !(kind in CONTINUED_WHY)
    ) {
      return;
    }
    for (const later of events.slice(index + 1)) {
      if (later.payload.role !== event.payload.role) continue;
      if (later.event_type === "RoundContinued") found.add(event.seq);
      if (later.event_type !== "HandoverWritten") break;
    }
  });
  return found;
}

/** A long task text (a continuation prompt carries the whole handover) is cut for the row. */
export const LONG_TEXT = 280;

/** Why a command was refused, from the rule id the daemon journals. */
export function refusalReason(rule: string): string {
  if (rule.startsWith("never_allowed:")) {
    const what: Record<string, string> = {
      privilege_escalation: "it would gain privileges",
      force_push: "it would rewrite history on a remote",
      pipe_to_shell: "it pipes a download into a shell",
      write_outside_root: "it changes something outside the project folder",
    };
    const key = rule.slice("never_allowed:".length);
    return `always blocked: ${what[key] ?? key}`;
  }
  const reasons: Record<string, string> = {
    no_shell_allowed: "this mode runs no commands on its own",
    no_matching_allow_entry: "it is not on the allowed list",
    not_a_plain_word_list: "it uses shell syntax that cannot be matched to the allowed list",
    not_a_sh_c_command: "it was not a plain shell command",
    argument_escapes_root: "an argument points outside the project folder",
    command_length: "the command line was empty or too long",
    unsafe_flag: "it used a flag that writes or runs other things",
    write_outside_root_never: "it writes outside the project folder",
    unknown_kind: "it asked for something this daemon does not allow",
    edit_outside_root: "it writes outside the project folder",
    cwd_outside_root: "it ran outside the project folder",
    tool_not_granted: "this role may not use that tool",
    file_change_not_granted: "it asked to write more than the project folder",
  };
  return reasons[rule] ?? rule;
}

const PREPARE_FAILURES: Record<string, string> = {
  exit: "the install command failed",
  timeout: "it took too long and was stopped",
  tool_missing: "the tool it needs is not installed",
  cancelled: "you stopped the team while it was installing",
};

/** The ecosystem and, for a project in a subfolder, where: `node (web/)`. */
function ecosystemLabel(p: Record<string, unknown>): string {
  return p.path && p.path !== "." ? `${p.ecosystem} (${p.path}/)` : String(p.ecosystem);
}

function preparedText(p: Record<string, unknown>): string {
  const seconds = Number(p.duration_s).toFixed(1);
  if (p.ok) return `Installed the ${ecosystemLabel(p)} dependencies in ${seconds}s.`;
  const why = PREPARE_FAILURES[String(p.failure)] ?? String(p.failure);
  return `Couldn't install the ${ecosystemLabel(p)} dependencies: ${why}.`;
}

const STOP_REASON = /^stop=\w+/;

const CONTINUED_WHY: Record<string, string> = {
  max_turns: "The round reached its turn limit",
  budget_exhausted: "The round used up its token budget",
  round_timeout: "The round ran past its time limit",
  context_pressure: "The round's context was nearly full",
  refused: "A command was refused and the round ended",
};

const FAILURE_KINDS: Record<string, string> = {
  max_turns: "It used all its turns before finishing",
  verification_failed: "Its own check of the work (the project's tests) failed",
  budget_exhausted: "It reached the token or cost limit",
  cancelled: "It was cancelled",
  context_pressure: "Its context was nearly full, so cuttlefish ended the round",
  round_timeout: "It ran past the time limit for one round, so cuttlefish stopped it",
  environment_stuck:
    "It kept failing on the project's environment (a missing tool or package), so cuttlefish stopped it",
  provider_auth: "The model provider rejected the API key",
  provider_credits: "The model provider account is out of credit",
  provider_rate_limit: "The model provider kept rate-limiting it",
  provider_outage: "The model provider had an outage",
  provider_other: "The model provider could not be reached or refused the request",
  harness_error: "The agent program itself failed",
  agent_error: "The agent program stopped before the round finished, or could not start",
  open_failed: "The agent could not start its session (a bad model or a missing credential)",
  protocol_error: "The agent program and cuttlefish stopped understanding each other",
};

/** Why a round failed, in words. `kind` is the daemon's `failure_kind` (kopicode only today);
 * an older event has none, so fall back to the `stop=` word in the reason. The raw reason
 * follows in brackets so nothing is hidden. */
export function failureText(kind: unknown, reason: string): string {
  reason = reason.replace(/^DelegationError: /, "");
  const stop = /^stop=(\w+)/.exec(reason)?.[1];
  const known = FAILURE_KINDS[typeof kind === "string" ? kind : (stop ?? "")];
  return known ? `${known}. (${reason})` : reason;
}

export type RoundOutcome = {
  kind: "completed" | "refused" | "failed";
  text: string;
  /** The daemon's `failure_kind` for a failed round, when it gave one. */
  failureKind?: string;
};

const ROUND_TYPES: Record<string, RoundOutcome["kind"]> = {
  DelegationCompleted: "completed",
  DelegationRefused: "refused",
  DelegationFailed: "failed",
};

/** How `role`'s latest round ended, or null while it is running or before it started. */
export function latestRound(
  events: readonly EpisodicEventView[],
  role: string,
): RoundOutcome | null {
  let latest: EpisodicEventView | null = null;
  for (const event of events) {
    const type = event.event_type;
    if (event.payload.role !== role) continue;
    if (type in ROUND_TYPES || type === "DelegationStarted" || type === "TaskSubmitted") {
      latest = event;
    }
  }
  if (latest === null || !(latest.event_type in ROUND_TYPES)) return null;
  const failureKind = latest.payload.failure_kind;
  return {
    kind: ROUND_TYPES[latest.event_type],
    text: summarize(latest),
    ...(typeof failureKind === "string" ? { failureKind } : {}),
  };
}

const SHELL_PREFIX = "/bin/sh -c ";

/** A shell command as the model typed it, without the `/bin/sh -c` wrapper kopicode adds. */
export function commandText(detail: string): string {
  return detail.startsWith(SHELL_PREFIX) ? detail.slice(SHELL_PREFIX.length) : detail;
}

/** The end of an install's output, or the last output of a stuck agent's failing command, when
 * there is any: shown under the row, not in the sentence. */
export function installOutput(event: EpisodicEventView): string {
  if (event.event_type === "EnvironmentPrepared") return String(event.payload.tail ?? "").trim();
  if (event.event_type === "DelegationFailed") return String(event.payload.detail ?? "").trim();
  return "";
}

export function isRefusedCommand(event: EpisodicEventView): boolean {
  return event.event_type === "ConsentDecided" && event.payload.answer === "deny";
}

/** The request each `RequestRaised` opened, by id: what a later `RequestResolved` row needs to say
 * whose request it was and what it was about (the resolved event itself carries only the id). */
export function raisedRequests(events: readonly EpisodicEventView[]): Map<string, EpisodicEventView> {
  const found = new Map<string, EpisodicEventView>();
  for (const event of events) {
    if (event.event_type === "RequestRaised") found.set(String(event.payload.request_id), event);
  }
  return found;
}

/** The role a row belongs to: its own, or for an answer the role of the request it closed. */
export function eventRole(
  event: EpisodicEventView,
  raised: ReadonlyMap<string, EpisodicEventView>,
): string {
  const own = event.payload.role;
  if (typeof own === "string") return own;
  if (event.event_type !== "RequestResolved") return "";
  const role = raised.get(String(event.payload.request_id))?.payload.role;
  return typeof role === "string" ? role : "";
}

/** How a request ended, with the command or question it was about when the journal still has it. */
export function summarizeWith(
  event: EpisodicEventView,
  raised: ReadonlyMap<string, EpisodicEventView>,
): string {
  const text = summarize(event);
  if (event.event_type !== "RequestResolved") return text;
  const detail = raised.get(String(event.payload.request_id))?.payload.detail;
  return typeof detail === "string" && detail !== "" ? `${text}: ${commandText(detail)}` : text;
}

export function summarize(event: EpisodicEventView): string {
  const p = event.payload;
  switch (event.event_type) {
    case "TaskSubmitted":
      return String(p.text);
    case "DelegationStarted":
      return String(p.task_text);
    case "DelegationCompleted":
      return String(p.summary);
    case "DelegationRefused":
      return String(p.reason);
    case "DelegationFailed":
      return failureText(p.failure_kind, String(p.reason));
    case "SteeringMessage":
    case "SteeringQueued":
      return String(p.text);
    case "HandoverWritten":
      return String(p.summary);
    case "LlmCallCompleted": {
      // The summariser's own call, kept for what it cost; its prompt and answer are in the journal.
      const tokens = (value: unknown) => (typeof value === "number" ? value.toLocaleString("en-GB") : "?");
      const cost = typeof p.cost_usd === "number" ? `, $${p.cost_usd.toFixed(4)}` : "";
      return `Wrote the checkpoint with ${p.model} (${tokens(p.input_tokens)} tokens in, ${tokens(p.output_tokens)} out${cost}).`;
    }
    case "TaskCompleted":
      return String(p.result);
    case "TaskFailed":
      // A task ends when its last round failed; the round's own row already says why, so
      // don't repeat the raw `stop=... exit_code=...` a second time.
      return STOP_REASON.test(String(p.error))
        ? "The task ended because its last round failed."
        : String(p.error);
    case "EnvironmentPrepareStarted":
      return `${ecosystemLabel(p)}: ${(p.commands as string[][]).map((c) => c.join(" ")).join(" && ")} (${p.reason})`;
    case "EnvironmentPrepared":
      return preparedText(p);
    case "RoundContinued":
      return `${CONTINUED_WHY[String(p.reason)] ?? "The round ran out of room"} and was not stuck, so a fresh round started from the latest handover (${p.count} of ${p.limit}).`;
    case "TeamBranchMade":
      return `The team's work goes on ${p.branch}, not your checkout. Each role has a branch of its own.`;
    case "RoleMergeReported": {
      const files = Array.isArray(p.files) ? p.files.join(", ") : "";
      if (p.status === "conflict") {
        return `${p.role}'s changes conflict with the team branch${files ? ` in ${files}` : ""}. Nothing was resolved for you; its work stays on its own branch.`;
      }
      return p.status === "nothing"
        ? `${p.role} changed nothing this round.`
        : `${p.role}'s changes were merged into the team branch.`;
    }
    case "StopRequested":
      return "You asked to stop. The team ends when the current round does, and a message you sent that was not applied yet is dropped.";
    case "TeamStopped":
      return "You stopped the team. Roles that had not finished are stopped; starting again begins a new run.";
    case "TeamResumed":
      return "A daemon restart found this run still in progress and resumed it. A round that was running starts again from its beginning.";
    case "ToolCallRecorded":
      return p.tool === "ask"
        ? askSummary(String(p.detail))
        : `${p.tool} (${p.status}): ${p.detail}`;
    case "RequestRaised":
      return `${p.title}: ${commandText(String(p.detail))}`;
    case "RequestResolved":
      return OUTCOMES[p.resolution as RequestResolution] ?? String(p.resolution);
    case "ConsentDecided": {
      // A file edit is not a command: say so, and name the file, not "Command / Allowed".
      const what =
        p.kind === "run_shell"
          ? commandText(String(p.detail))
          : p.kind === "file_change"
            ? `edit of ${String(p.detail)}`
            : String(p.detail);
      return p.answer === "deny"
        ? `Refused ${what}. ${capitalise(refusalReason(String(p.rule)))}.`
        : `Allowed ${what}`;
    }
    default:
      return JSON.stringify(p);
  }
}

/** kopicode's `ask` tool. The row cannot say whether anyone answered (its status is "ok" either
 * way): with a kopicode that has the live wire (v0.4.0) the question card beside it says how it
 * ended, and an older kopicode always gave the model the fixed "nobody is here" reply. */
function askSummary(detail: string): string {
  return `Asked a question: ${questionOf(detail)}`;
}

/** kopicode's `detail` is the call's raw arguments, whitespace-collapsed and cut at 120
 * characters with a trailing "…", so a longer call is no longer valid JSON. Read the question out
 * of the cut text rather than showing the JSON. */
function questionOf(detail: string): string {
  try {
    const parsed: unknown = JSON.parse(detail);
    if (parsed && typeof parsed === "object" && "question" in parsed) {
      return String((parsed as { question: unknown }).question);
    }
  } catch {
    // not whole JSON: fall through
  }
  const cut = /^\{\s*"question"\s*:\s*"((?:[^"\\]|\\.)*)("?)/.exec(detail);
  if (!cut) return detail;
  let text = cut[1];
  try {
    text = JSON.parse(`"${text}"`) as string;
  } catch {
    // a cut escape sequence: keep the raw text
  }
  return cut[2] ? text : text.replace(/…$/, "") + "…";
}

function capitalise(text: string): string {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

/** A time for a log row: just the time today, the date as well on any other day. */
export function formatWhen(ts: string, now: Date = new Date()): string {
  const when = new Date(ts);
  const time = when.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  const sameDay =
    when.getFullYear() === now.getFullYear() &&
    when.getMonth() === now.getMonth() &&
    when.getDate() === now.getDate();
  return sameDay
    ? time
    : `${when.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

/** Whether a scrolled list is at (or within `slack` px of) its bottom, so it should keep following new rows. */
export function isPinnedToBottom(
  scrollTop: number,
  clientHeight: number,
  scrollHeight: number,
  slack = 24,
): boolean {
  return scrollHeight - (scrollTop + clientHeight) <= slack;
}

export type SteerFate = "waiting" | "applied" | "dropped";

/** What became of each message a person sent (`SteeringQueued`, by seq), read from the journal:
 * `applied` once the same role journals the same text as `SteeringMessage`, `dropped` when the
 * team stopped or that role's task ended first, else still `waiting` for the round to end. */
export function steerFates(events: readonly EpisodicEventView[]): Map<number, SteerFate> {
  const fates = new Map<number, SteerFate>();
  events.forEach((event, index) => {
    if (event.event_type !== "SteeringQueued") return;
    let fate: SteerFate = "waiting";
    for (const later of events.slice(index + 1)) {
      const sameRole = later.payload.role === event.payload.role;
      if (later.event_type === "SteeringMessage" && sameRole && later.payload.text === event.payload.text) {
        fate = "applied";
        break;
      }
      if (
        later.event_type === "TeamStopped" ||
        ((later.event_type === "TaskCompleted" || later.event_type === "TaskFailed") && sameRole)
      ) {
        fate = "dropped";
        break;
      }
    }
    fates.set(event.seq, fate);
  });
  return fates;
}

/** The seqs of `SteeringMessage` rows that repeat a `SteeringQueued` row already in the log: the
 * queued row says "(applied)", so a second "You" row with the same words adds nothing (CUT-167).
 * A message with no queued row before it (sent from the command line) is not in the set. */
export function appliedSteerRows(events: readonly EpisodicEventView[]): Set<number> {
  const hidden = new Set<number>();
  events.forEach((event, index) => {
    if (event.event_type !== "SteeringQueued") return;
    const match = events
      .slice(index + 1)
      .find(
        (later) =>
          later.event_type === "SteeringMessage" &&
          !hidden.has(later.seq) &&
          later.payload.role === event.payload.role &&
          later.payload.text === event.payload.text,
      );
    if (match) hidden.add(match.seq);
  });
  return hidden;
}

/** The seqs of `TaskCompleted` rows whose result is the very text its role's last round already
 * showed as "Round done" (CUT-165). The row says so instead of printing it twice. */
export function repeatedFinishes(events: readonly EpisodicEventView[]): Set<number> {
  const repeated = new Set<number>();
  const lastSummary = new Map<string, string>();
  for (const event of events) {
    const role = String(event.payload.role ?? "");
    if (event.event_type === "DelegationCompleted") lastSummary.set(role, String(event.payload.summary));
    if (event.event_type === "TaskCompleted" && lastSummary.get(role) === String(event.payload.result)) {
      repeated.add(event.seq);
    }
  }
  return repeated;
}

export const REPEATED_FINISH_TEXT = "The task is finished. Its last round's summary is above.";
const ROUND_END_TYPES = new Set(["DelegationCompleted", "DelegationRefused", "DelegationFailed"]);

/** The seqs of round-end rows (done, refused, failed) for a round that was still running when Stop
 * was pressed (CUT-166). Such a round ends because it was cut off, not because the agent judged
 * the task done, and what the agent said last can read like a normal finish ("Blocked by the
 * workspace execution gate": Stop refuses every later request). Read from the journal's order:
 * a `StopRequested` between the role's latest `DelegationStarted` and the row. */
export function stoppedRounds(events: readonly EpisodicEventView[]): Set<number> {
  const found = new Set<number>();
  const started = new Map<string, number>();
  let lastStop = -1;
  events.forEach((event, index) => {
    const role = String(event.payload.role ?? "");
    if (event.event_type === "DelegationStarted") started.set(role, index);
    else if (event.event_type === "StopRequested") lastStop = index;
    else if (ROUND_END_TYPES.has(event.event_type) && lastStop > (started.get(role) ?? Infinity)) {
      found.add(event.seq);
    }
  });
  return found;
}

export const STOPPED_ROUND_NOTE = "You pressed Stop while this round ran.";

export const STEER_FATE_TEXT: Record<SteerFate, string> = {
  waiting: "sent; applies when the round ends",
  applied: "applied",
  dropped: "not applied: the run ended first",
};
