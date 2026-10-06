// Plain-language labels for the activity log. The journal's event names and rule ids are the
// daemon's vocabulary; the person reading the log should not need them.

import type { EpisodicEventView } from "./api";

const LABELS: Record<string, string> = {
  TaskSubmitted: "Task",
  DelegationStarted: "Round started",
  DelegationCompleted: "Round done",
  DelegationRefused: "Refused",
  DelegationFailed: "Failed",
  SteeringMessage: "You",
  HandoverWritten: "Checkpoint",
  TaskCompleted: "Finished",
  TaskFailed: "Failed",
  TeamResumed: "Resumed",
  ToolCallRecorded: "Tool",
  ConsentDecided: "Command",
};

export function eventLabel(type: string): string {
  return LABELS[type] ?? type;
}

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
    not_a_plain_word_list: "chained or quoted commands are not allowed in Standard",
    not_a_sh_c_command: "it was not a plain shell command",
    argument_escapes_root: "an argument points outside the project folder",
    command_length: "the command line was empty or too long",
    unsafe_flag: "it used a flag that writes or runs other things",
    write_outside_root_never: "it writes outside the project folder",
    unknown_kind: "it asked for something this daemon does not allow",
  };
  return reasons[rule] ?? rule;
}

const SHELL_PREFIX = "/bin/sh -c ";

/** A shell command as the model typed it, without the `/bin/sh -c` wrapper kopicode adds. */
export function commandText(detail: string): string {
  return detail.startsWith(SHELL_PREFIX) ? detail.slice(SHELL_PREFIX.length) : detail;
}

export function isRefusedCommand(event: EpisodicEventView): boolean {
  return event.event_type === "ConsentDecided" && event.payload.answer === "deny";
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
      return String(p.reason);
    case "SteeringMessage":
      return String(p.text);
    case "HandoverWritten":
      return String(p.summary);
    case "TaskCompleted":
      return String(p.result);
    case "TaskFailed":
      return String(p.error);
    case "TeamResumed":
      return "A daemon restart found this run still in progress, and it picked up where it left off.";
    case "ToolCallRecorded":
      return `${p.tool} (${p.status}): ${p.detail}`;
    case "ConsentDecided": {
      const what = p.kind === "run_shell" ? commandText(String(p.detail)) : String(p.detail);
      return p.answer === "deny"
        ? `Refused ${what}. ${capitalise(refusalReason(String(p.rule)))}.`
        : `Allowed ${what}`;
    }
    default:
      return JSON.stringify(p);
  }
}

function capitalise(text: string): string {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

/** A time for a log row: just the time today, the date as well on any other day. */
export function formatWhen(ts: string, now: Date = new Date()): string {
  const when = new Date(ts);
  const time = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const sameDay =
    when.getFullYear() === now.getFullYear() &&
    when.getMonth() === now.getMonth() &&
    when.getDate() === now.getDate();
  return sameDay
    ? time
    : `${when.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}
