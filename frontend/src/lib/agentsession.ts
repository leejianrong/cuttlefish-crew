// The Agent session card's words (CUT-95): what state the tmux session is in, and which of
// three things happened when it last came up. Kept out of the component so it is unit-tested.

import type { AgentSession } from "./api";

export const BACKEND_LABELS: Record<string, string> = {
  "claude-code": "Claude Code",
  codex: "Codex",
  kopicode: "kopicode",
};

export function backendLabel(backend: string | null): string {
  return backend ? (BACKEND_LABELS[backend] ?? backend) : "the agent";
}

/** One sentence on how the session last came up, or null when it never has. */
export function lastActionText(session: AgentSession): string | null {
  const who = backendLabel(session.backend);
  switch (session.last_action) {
    case "started":
      return `Started a new ${who} session.`;
    case "reattached":
      return `Found ${who} still running in tmux and reattached to it. Nothing was restarted.`;
    case "relaunched":
      return `tmux had stopped, so cuttlefish started ${who} again with its resume command. The conversation carries on.`;
    default:
      return null;
  }
}

export function stateText(session: AgentSession): string {
  switch (session.state) {
    case "running":
      return "Running in tmux";
    case "stopped":
      return "Stopped";
    default:
      return "Not started";
  }
}

export function explanation(session: AgentSession): string {
  if (session.state === "none") {
    return "Keeps one agent running in tmux for this project. It stays up when you close the browser or the daemon restarts, and comes back with its own resume command after a reboot.";
  }
  if (session.state === "stopped") {
    return "Start it to pick the conversation up where it left off.";
  }
  return "Permission questions appear in the session's own terminal, not under Needs you.";
}
