# UX review of the shipped dashboard (V4-G, 2026-10-06)

One reviewer (a single read-only agent) walked five task flows against the built screens at
1280 and 390 wide, light and dark, and read the code. What it could not check: a live running
team (no agent CLI), a screen reader, and current Claude Projects / Paperclip UIs (those
comparisons are from memory and from `docs/research/paperclip-comparison.md`). Contrast figures
are computed from token values, not measured on pixels.

## Fixed in V4-G

- Unsaved Permissions edits no longer vanish when you switch tabs (tabs stay mounted); a pinned
  "unsaved changes" bar keeps Save and Discard in reach, above the phone nav bar; a reload warns.
- Team edits are kept per role (a role with edits shows "Unsaved"); Remove role, Remove project
  and Stop team ask first, in plain words (the folder is not touched; starting again begins a
  new run).
- Ticket and ADR ids, "fleet view", "--" and lowercase errors are gone from visible copy; the
  page title is "cuttlefish-crew".
- The activity log uses plain labels, shows refused commands as "Refused `x`. It is not on the
  allowed list." with a "Change permissions" link, and puts sequence numbers in a tooltip.
- The Overview "Start a team" form is in the new design system (outlined fields, a switch for
  approvals, role prompts folded away) with a one-line "Standard mode, builder, reviewer"
  summary and three starter tasks; all tabs share one page width.
- Faint text meets AA in the light theme (`--text-faint` is `on-surface-variant`).
- Radio groups (templates, modes) are one Tab stop and move with the arrow keys; Connect errors
  are announced.
- Copy: Auto is "any command, except the always-blocked list"; kopicode is "Decided live, no
  prompt yet"; backends are named "Claude Code" and "Codex"; Reset says it only changes the
  editor until you Save; "Start…" is "Open".
- A Go or Rust folder switches the Go and Rust group on; the summary says so.
- A partial Permissions save says which part was saved; Stop/Remove failures show an error;
  a lost connection on a project page shows a banner; an empty Projects screen is a card with an
  "Add your first project" button; the mobile Add project button is pinned.

## Later (not done)

- **Real "Needs you"** with Allow once / Always allow that turns a refusal into a rule in one
  click (V4-H), including a per-role refusal count on role cards. Today's log link is the stopgap.
- A blocked role's approval card should say why it is blocked (review, budget, refusal) from the
  event stream; today it explains the three possibilities.
- One-request permission save; URL routing and browser history (a project cannot be linked, Back
  leaves the app); a `#token=` link printed by `serve` to skip the manual token step.
- "Recent" folders from disk, not only registered projects; the Add screen's "Agent backend:
  Daemon default" should name the default; a role diff against its built-in prompt.
- Touch targets: switches and `.btn.sm` are 32px high (M3's own size) but below 44px on a phone;
  the command chip's delete icon is about 14px.
- Make Roles a chooser inside Add project and Team rather than a rail destination; move Sprites
  out of the rail before a "Needs you" destination wants the slot.
- Per-role command groups ("Tester: Standard + containers" in the mockup).
- Overview of a running team (steer and approve cards, stale status after a crash) was reviewed
  from code only; check it with a real agent.

## Against Claude Projects and Paperclip

Better: built-in roles and prompts instead of a blank page, folder picker with git state and a
"can't undo edits" warning, per-backend honesty, the always-blocked list. Worse: first-run
guidance (Paperclip interviews you and proposes a team; this has starter chips only). To copy
when Needs-you ships: "Allow once / Always for this project" feeding the same "Your own commands"
list. Not to copy: Paperclip's vocabulary (org, hiring, issues) or Claude Code's "bypass" naming.
