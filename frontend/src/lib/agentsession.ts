// The Agent session card's words (CUT-95): what state the tmux session is in, and which of
// three things happened when it last came up. Kept out of the component so it is unit-tested.

import type { AgentSession } from "./api";

export const BACKEND_LABELS: Record<string, string> = {
  "claude-code": "Claude Code",
  codex: "Codex",
  kopicode: "kopicode",
  terminal: "Terminal command",
};

/** What a project that runs its own command is told, on the page and above the terminal
 * (CUT-233, ADR-0047). cuttlefish reads nothing from such a tool, so it never implies it does. */
export const TERMINAL_COMMAND_NOTE =
  "cuttlefish does not know this tool, so it has no live status, no chat, no goal and no Needs you cards for it. Anything it asks appears only in the Terminal tab.";

export function isTerminalCommand(session: Pick<AgentSession, "backend">): boolean {
  return session.backend === "terminal";
}

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
      return isTerminalCommand(session)
        ? "tmux had stopped, so cuttlefish ran the command again from the start."
        : `tmux had stopped, so cuttlefish started ${who} again with its resume command. The conversation carries on.`;
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
  if (isTerminalCommand(session) && session.state === "running") return TERMINAL_COMMAND_NOTE;
  if (session.state === "none") {
    return "Keeps one agent running in tmux for this project. It stays up when you close the browser or the daemon restarts, and comes back with its own resume command after a reboot.";
  }
  if (session.state === "stopped") {
    return "Start it to pick the conversation up where it left off.";
  }
  if (session.asks === "none") return TERMINAL_COMMAND_NOTE;
  if (session.asks === "needs-you") {
    return "Permission questions appear under Needs you, and in the session's terminal too. Answer in either place.";
  }
  if (session.asks === "auto") {
    return "This session was started in Auto mode, so it never asks and nothing will appear under Needs you. Stop it and start it again after changing the mode.";
  }
  return "Permission questions appear in the session's own terminal, not under Needs you.";
}
