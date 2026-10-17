// The terminal tab's plumbing (CUT-96): the websocket address, the first message that signs in, what
// the phone's key row sends, and what a closed connection means. Pure, so it is unit-tested.

/** `http://host:8420` becomes `ws://host:8420`, `https` becomes `wss`. */
export function terminalUrl(baseUrl: string, projectId: string): string {
  const base = baseUrl.replace(/\/+$/, "").replace(/^http/, "ws");
  return `${base}/api/projects/${encodeURIComponent(projectId)}/terminal`;
}

/** The first message. A browser cannot set a header on a websocket and a token in the address would
 * reach logs, so the token goes in the opening frame. */
export function authMessage(token: string, cols: number, rows: number): string {
  return JSON.stringify({ type: "auth", token, size: { cols, rows } });
}

export function resizeMessage(cols: number, rows: number): string {
  return JSON.stringify({ type: "resize", cols, rows });
}

/** Keys a phone keyboard does not have, sent as the bytes a terminal expects. */
export const KEY_ROW: { label: string; aria: string; send: string }[] = [
  { label: "Esc", aria: "Escape", send: "\x1b" },
  { label: "Tab", aria: "Tab", send: "\t" },
  { label: "Ctrl-C", aria: "Control C, interrupt", send: "\x03" },
  { label: "↑", aria: "Up arrow", send: "\x1b[A" },
  { label: "↓", aria: "Down arrow", send: "\x1b[B" },
  { label: "←", aria: "Left arrow", send: "\x1b[D" },
  { label: "→", aria: "Right arrow", send: "\x1b[C" },
  { label: "Enter", aria: "Enter", send: "\r" },
];

export type CloseKind = "unauthorised" | "no-session" | "lost" | "ended";

export interface CloseInfo {
  kind: CloseKind;
  text: string;
}

/** What a closed socket means, in words. 4401 and 4404 are the daemon's own codes. */
export function describeClose(code: number, reason: string): CloseInfo {
  if (code === 4401) {
    return {
      kind: "unauthorised",
      text: "The daemon did not accept this sign-in. Sign in again and the terminal reconnects.",
    };
  }
  if (code === 4404) {
    return {
      kind: "no-session",
      text: reason || "No agent session is running for this project.",
    };
  }
  if (code === 1000 || code === 1005) {
    return {
      kind: "ended",
      text: "The terminal closed. The agent is still running in tmux.",
    };
  }
  return {
    kind: "lost",
    text: "The connection to the daemon was lost. The agent is still running in tmux.",
  };
}
