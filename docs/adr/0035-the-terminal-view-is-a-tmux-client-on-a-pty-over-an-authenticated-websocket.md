# ADR-0035: The terminal view is a tmux client on a pty, over an authenticated websocket

- Status: Accepted
- Date: 2026-10-16
- Deciders: Jian

## Context

ADR-0032 requires the real terminal to stay viewable and usable even after a structured chat view exists, from a
phone as well. ADR-0034 puts each project's agent in tmux. The vendor-neutral way to show a TUI in a browser is a
terminal emulator (xterm.js) fed by a pty.

## Decision

**`GET /api/projects/{id}/terminal` is a websocket. The daemon runs `tmux attach` on a pty of its own and pumps
bytes both ways.**

- Each browser tab is its own tmux client, so several can watch one session and tmux sizes it to the smallest.
  Closing the tab detaches the client; the agent keeps running (ADR-0032). The view never starts a session: with
  none running it closes with code 4404 and the dashboard offers "Start the agent session".
- **Auth:** a browser cannot set a header on a websocket, and a token in the URL would reach logs, so the first
  message is `{"type":"auth","token":...,"size":{...}}`. It goes through the same `SecurityCheck` as every other
  route, with the handshake's own `Host` and `Origin`, so a page on another site cannot drive it. Failure closes
  with 4401, a malformed or missing first message with 4400, and nothing is read from the pty before it passes.
- **Wire:** after `{"type":"ready"}`, binary frames are terminal bytes in both directions, and a text frame
  `{"type":"resize","cols":N,"rows":N}` resizes. No new protocol on the agent's side: this only carries what tmux
  draws and what a person types (ADR-0003, ADR-0005).
- The dashboard's Terminal tab uses xterm.js with its fit addon, takes its colours from the `--md-sys-*` roles, and
  has a key row (Esc, Tab, Ctrl-C, arrows, Enter) for a phone keyboard. tmux's own status line is off for sessions
  cuttlefish creates.

## Consequences

- **This is typed access to an agent that can run commands as the daemon's user.** It grants nothing the dashboard
  did not (a person who can start a team can already make an agent run commands), but it is more direct, and it
  bypasses the consent policy: what a person types into the TUI is theirs to decide. The token (loopback) or the
  password session (non-loopback, ADR-0011) is the whole guard, so a non-loopback bind must keep its password.
- A tmux-hosted agent's permission prompts are answered here, in the terminal (ADR-0034).
- The pty bridge is POSIX only (`pty`, `termios`), as tmux is.
- Binary output is not interpreted by the daemon, so nothing redacts a secret an agent prints into this view. The
  episodic journal is a different path and is unchanged (ADR-0004).
