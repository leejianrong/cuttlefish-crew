# Dashboard redesign: target design (mockup)

Agreed 2026-10-06. The target for the dashboard's next UI pass (slices V4-D onward in
`docs/SLICES.md`). These are mockups, not shipped code. Where the shipped dashboard
and this folder disagree after a slice lands, update this folder.

## Where the designs live

- **Canvas (source of truth for how it looks):** https://claude.ai/artifact/7zrJpL6TfKfy3RrpJCrZyz
  (private to its owner; share from the page's Share menu).
- **This folder (source of truth for tokens and markup):** `m3.css` holds every design
  token and the component classes; the four `*.dc.html` files are the screens. They are
  Design Component files, so they only render inside the canvas (they need its
  `support.js`). Read them for structure, copy, and exact values.
- `canvas.json` is the canvas index (frame positions); only needed to re-publish.

| File | Screen |
| --- | --- |
| `1-add-project.dc.html` | Add a project: folder picker, team template, permission level, collapsed Advanced |
| `2-project-needs-you.dc.html` | Project page, "Needs you" tab: permission requests, agent questions, blocked actions |
| `3-permissions.dc.html` | Permissions tab: modes, command groups, always-blocked list, per-backend behaviour |
| `4-roles-and-teams.dc.html` | Roles and team templates with editable default prompts |

## Design decisions to carry over

- **Material Design 3 tokens, wired as roles, never raw values.** Color roles
  (`--md-sys-color-*`), type scale, shape scale, elevation levels, state layers
  (hover .08, focus .12), standard easing. All in `m3.css`. Seed color is teal
  (`#00696f` primary), not M3's baseline purple. Light theme only so far; a dark scheme
  must be derived from the same seed, not hand-darkened.
- **Coral (the custom `attention` role) means "needs you", and only that.** Used for the Fleet
  badge in the nav rail, the "Needs you" tab, request tags and the Default/Recommended
  tags. Do not spend it on decoration.
- **Type:** Bricolage Grotesque for the brand slot (display, headline, title-large),
  Figtree for the plain slot, JetBrains Mono for commands and paths.
- **Short main flow, optional things hidden.** Adding a project is folder, team,
  permission level, go. Backend, budgets, custom commands and secrets sit in a collapsed
  "Advanced options" row.
- **Defaults beat configuration.** Built-in roles with real prompts and permission
  presets; team templates (Solo builder, Builder + reviewer as default, Full crew); a
  "Default prompt" marker with "Reset to default".
- **Permission modes:** Ask first, Standard (default), Auto. Auto still keeps a hard
  never-allowed list: `sudo`, `rm -rf` outside the folder, `git push --force`,
  `curl ... | sh`, and writes outside the project folder.
- **Say what each backend can actually do.** kopicode pauses live; Claude Code and
  Codex cards state when an answer lands ("Delivered when this turn ends", "Takes
  effect next round") until their live-prompt slices ship, and the Permissions tab has
  a "how each agent receives this" panel. Never imply a live prompt where there is none.
- **Plain, specific copy.** Buttons say what happens ("Allow once", "Allow and rerun
  round"). No ticket numbers or internal ids in the UI.
- Real `<button>`, `<input>` and `<label>` elements, icon-only buttons have an
  `aria-label`, visible focus ring, reduced motion respected.

## Built since the mockup

- **V4-D (theme and shell) shipped.** Source of truth for the look is now
  `frontend/src/theme-tokens.css` (generated roles, light and dark) and `theme.css` (scales,
  primitives); `m3.css` here is only the mockups' copy. Roles come from
  `@material/material-color-utilities` (`SchemeTonalSpot`, seed `#00696f`). The mockup's
  coral was hand-picked and sat on the tertiary role; the generated tertiary is a muted
  blue-grey, so coral is a custom `attention` role (and `success` another) harmonised to the seed.
  Regenerate both schemes together; never hand-edit a role.
- Screenshots of what shipped (V4-D, projects screen in light, dark and phone width) are in
  `shipped/`. They are the baseline to compare later slices against.
- **V4-E (folder picker and the short register flow) shipped.** `AddProject.svelte` follows
  screen 1: folder browser with breadcrumbs, up button, git tag per folder and a typed-path box;
  team templates; the three modes; a summary column with git state and detected languages; and
  collapsed Advanced options. Screenshots in `shipped/v4e-*`. Differences from the mockup:
  no "New folder" button; "Recent" lists the folders of already-registered projects; the
  summary column stacks below the form under 900px. Registering opens the new project's page.
- **V4-F (Permissions and Team tabs) shipped.** A project page now has Overview, Permissions
  and Team tabs; the **Roles** rail destination is a read-only library. Screenshots in
  `shipped/v4f-*`. Differences from the mockups: the roles editor lives on the project (roles
  belong to a project, so editing a "global" role would be a fiction), and the library only
  shows the built-ins; the "Always blocked", "How each agent receives this" and "Role overrides"
  panels are driven by `GET /api/permissions`, and the role overrides panel is read-only (edit
  on Team); Save and Discard are explicit; a running team shows that changes apply at the next
  start. "Tester: Standard + containers" per-role command groups are not built: command groups
  are per project.
- **V4-G (UX review) shipped.** Findings, what was fixed and what was deferred are in
  `ux-review-v4g.md`. The `shipped/` screenshots were retaken after the fixes and now include the
  Connect screen and the Overview tab. Overview's "Start a team" follows the design system and
  a pinned bar carries Save/Discard on Permissions and "Add project" on narrow screens.
- Fonts are self-hosted (`@fontsource-variable/*`), so the dashboard makes no external request.
- The nav rail lists only screens that exist (`frontend/src/lib/nav.ts`); Roles and Needs you
  joined it with V4-F and V4-H. The rail becomes a bottom bar under 640px. A theme button
  cycles system, light, dark and is remembered per browser.

- Stored role prompts open with the instruction, not "You are the builder.", because
  cuttlefish prepends "You are {name}." to every task. The prompt shown in
  `4-roles-and-teams.dc.html` should drop that opener when the screen is built.
- The builder prompt's "ask one specific question" line is not in the shipped prompt:
  only kopicode can pause for a permission and no backend can take an agent's question
  mid-run today (kopicode#173), so it says to state the assumption and take the
  most conservative reading. Restore the question once asking works on every backend.
- Project mode is `ask-first`, `standard` or `auto`; a role's `access` is null (inherit),
  one of those, or `read-only`. Read-only blocks edits on Claude Code and Codex only, so the
  UI must not call it "can't edit" without the per-backend caveat. Ask first and Standard ask
  only on kopicode; say so wherever a mode is described, never "asks" without it.
- Auto is answered by cuttlefish (`ConsentPolicy(auto=True)`), so it is available on kopicode
  now; it needs the `serve` transport.
- `make` commands are on by default (not in the Permissions mockup).

- **V4-H (Needs you) shipped.** Screenshots in `shipped/v4h-*`. A project page has a **Needs you**
  tab with a coral count; the rail has a **Needs you** destination (the mockup called it Fleet)
  with the total waiting across projects and a screen that groups them by project. A permission
  card shows the command in mono, why the agent stopped, Allow once, Deny, a countdown ("Denies on
  its own in 4:26", coral and bold under 30 s) and an editable "Always allow commands that start
  with" field that is disabled unless it is the start of the command; a command with shell syntax
  offers only Allow once and says why. Answered requests collapse to rows under "Answered recently".
  Differences from the mockup: only the permission card exists (question and blocked cards come
  with V4-I and the Claude Code and Codex slices, and the model already has the kinds); there is
  no per-card "Allow and rerun round"; the rail badge and tab count are polled every 2.5 s with the
  rest of the page, and the countdown runs client-side between polls. A kopicode `ask` call shows
  in Recent activity as "Asked a question nobody could answer". The project tab and Add project
  say Ask first and Standard ask **on kopicode only**.

- **V5-E1c (failed rounds) shipped.** A failed Start shows the daemon's own reason. A failed round
  reads "Round failed" with the failure in words and the raw `stop=...` in brackets, a mono "Full output"
  line with the backend's record path, and the task's own row says "The task ended because its last round
  failed" instead of repeating the reason. A blocked role shows Approve and Reject only when a review
  gate or a usage limit is waiting on a person; otherwise it shows a note that the task ends unless
  you send a message (never offer a decision nobody is waiting for).

- **V5-E2 (Environment card) shipped.** The project Overview has a read-only **Environment** card
  above Recent activity: one row per ecosystem found at the project root (Python, Node, Go, Rust, Java,
  Ruby) with its package tool, the version the files ask for and the lockfile, and where the project keeps
  an install in its own folder, "`.venv` is there" (success role) or "`.venv` is missing". A missing install
  adds one line saying cuttlefish does not install dependencies yet. An empty result says subfolders are not
  scanned. Not in the mockups; no coral (nothing here needs a decision). Fetched once per visit.

- **V5-E3b (installing dependencies) shipped.** Start asks before it installs, when the project's setting is
  "Ask me" and something is missing or stale: a **Install dependencies first?** card in the start form lists
  each ecosystem, why, and the exact commands, says it runs the project's own install scripts in the project
  folder, and offers Install and start, Install and do this automatically (sets the project to Automatically),
  Start without installing, and Cancel. The Environment card gains a three-way **Install dependencies before a team
  starts** control (Ask me, Automatically, Never) with a sentence per choice, and its missing-install note follows
  the setting. While cuttlefish installs, the Overview shows a status line with the command and the reason, and Stop
  team cancels the install. Install rows in Recent activity read "Installing" and "Install done", with the end of the
  output under a collapsed **Output**. Neutral surfaces throughout: nothing here needs a decision from you in the
  coral sense once you have chosen, so coral stays for Needs you.

## Known gaps in the mockup

- Static: no working interactions, no empty or error states, no dark theme, no phone
  layout (the shipped dashboard must work at phone width).
- The project page in screen 2 uses a Full crew team while screen 1 selects Builder +
  reviewer; they are different projects.
- No screenshots are checked in; the canvas is the visual reference.

- **V5-limit-settings (Team tab) shipped.** A **Limits for this project** card under Roles and a **Limits for {role}**
  disclosure in each role's editor (open when the role sets any). Six rows from `GET /api/limits`: title, one-line summary,
  "Inherits 100 turns from the project" or "Set here: 40 turns", a number box whose placeholder is what it inherits, a
  note when 0 means something ("0 means no limit"), and "Use the project's" / "Use the default" to clear. An invalid entry
  says why in words and is not saved. Neutral surfaces, no coral. Not in the mockups.

- **V5-routing shipped.** The URL hash says where you are: `#/projects`, `#/projects/<id>` (and `/team`, `/permissions`, `/needs-you`
  for a tab), `#/add`, `#/needs-you`, `#/roles`, `#/sprites`. Reload keeps the screen, Back and Forward walk through the screens
  and tabs you visited, and a link can be pasted into a chat (it still needs the daemon's token, which is kept per browser).


- **Question buttons and last-run card shipped (2026-10-10).** A question card shows the agent's choices as tonal buttons above the text box
  ("Or write your own answer"); one click sends that choice. When nothing is running and the last run ended, Overview opens with a card
  "The last run finished / failed / was stopped" with files changed, minutes, tokens and cost (cost left out when no backend reported one),
  and a failed role's reason in plain words (CUT-162: the journal's `failure_kind`, `agent_error` when the agent program died or never started; the raw text follows in brackets) with a tonal "Try again with the same task" button that starts the failed roles again with the task each was given (the form is filled first, so a refused start leaves the text to edit), then a "<role> said" block per role that completed (CUT-63): its last delegation summary, which now carries
  the model's last message for kopicode, Claude Code and Codex alike (capped at 600 characters, scrolls past about 12 lines); it sits above Start a team. Neutral surface, an error-coloured edge only when it failed. Not in the mockups.

- **The activity log no longer repeats itself (CUT-165, CUT-167).** A steer is one "You" row: the message the dashboard applied at the round
  boundary is hidden when the queued row before it already says "(applied)" (a steer sent from the command line has no queued row, so it
  stays). A "Finished" row whose result is the same text as that role's last "Round done" says "The task is finished. Its last round's
  summary is above." The journal is unchanged; both are read-time choices in `lib/events.ts`.
- **A round ended by Stop is labelled (CUT-166).** In the activity log a round-end row (done, refused or failed) for a round that was running when
  Stop was pressed reads "Round stopped" and starts "You pressed Stop while this round ran.", so an agent's last words ("Blocked by the workspace
  execution gate") are not mistaken for a normal finish. Read from the order of events, not stored. On Codex the daemon now interrupts the turn
  on Stop (0.3 s seen live), so its round is a failed, cancelled one.

- **Project Secrets tab shipped (CUT-80, 2026-10-10).** A fifth project tab, after Team. Two cards of sealed rows (a lock, the name in mono, twelve
  dots whatever the value's length, an "Agent key" tag where a backend runs on it, a line saying whether its commands can see it), Replace and Remove
  as text buttons, Add secret as the one filled button. Add and Replace are a modal `<dialog>` with outlined fields (the value is a password field
  and is cleared whenever the dialog closes); Remove asks first. Key not set shows the three steps instead of a form. Teal and neutral surfaces,
  no coral. Differences from the proposal: no "Who can use it" switch in the dialog (shared secrets get their own screen, CUT-81, so a shared key
  is never changed from inside one project), and the saved/removed message is a line in the tab, not a snackbar.

- **Shared Secrets screen shipped (CUT-81, 2026-10-10).** A **Secrets** destination in the nav rail (lock icon, after Roles), `#/secrets`. One card of sealed rows
  with Replace and Remove, **Add shared secret** as the one filled button, an "Overridden in N projects: a and b" tag where projects keep their own value, and the
  same key-not-set steps as the project tab. Remove names how many projects lose it. Add and Replace use the same dialog as the project tab (`SecretDialog.svelte`).
  Add project's Advanced options ends with a line pointing at both places, with a link to this screen that opens in a new tab so the form is not lost.
- **Credential rows say who can see the key (CUT-86, 2026-10-10).** An agent-credential row's note follows the daemon's `broker` flag (in the secrets payloads): with `CUTTLEFISH_CREDENTIAL_BROKER=1` Claude Code's and Codex's rows read "Held by cuttlefish: its commands see only a short-lived token"; with it off, Claude Code's reads "Its commands can see this key". The Permissions tab's backend notes follow the same flag.
- **Inline approvals (CUT-88, 2026-10-11).** A pending request is also shown at the top of the project's
  Overview, as the same card the Needs you tab uses, so answering does not mean changing tab. The tab
  stays for history and for the fleet-wide Needs you screen.
- **Connection lost (CUT-67, 2026-10-12).** While the daemon cannot be reached, every request card's buttons are
  disabled, its countdown stops ("Countdown paused") and a note says to restart `cuttlefish serve` and that a request
  open at a restart is abandoned (ADR-0028).
- **A role waiting on a request, and steering in the log (CUT-66, 2026-10-12).** A role the daemon still
  reports as working while a command or question holds it shows a "waiting for you" chip (the existing
  blocked colour) and a one-line note pointing to Needs you. The steer box keeps a permanent hint under it
  (applies at the end of the round, stopping first drops it) instead of a placeholder that vanishes. A sent
  steer is journaled at once as `SteeringQueued` and shows in the activity log as "You" with what became of
  it: sent, applied (at once for a Codex role, else at the round boundary), or not applied because the run ended first.
- **Agent session card (CUT-95, 2026-10-16).** The project Overview has an **Agent session** card above the team
  form: the state (Running in tmux, Stopped, Not started) with the backend's name, Start, Resume or Stop session,
  one sentence on how it last came up (started a new session, found it still running and reattached, or tmux had
  stopped and it was started again with its resume command), and a note that permission questions appear in the
  session's own terminal, not under Needs you. Neutral surface, success colour only on the running state; no coral
  (nothing needs a decision). It reads again every five seconds.
- **Artifacts tab (CUT-97, 2026-10-16).** After Terminal. A list of the files in `.cuttlefish/artifacts/` (path,
  kind, size, how long ago; newest first) beside a reading pane; under 720px the list stacks above it and scrolls
  within 14rem. Markdown is drawn from a parsed tree with the theme's roles (no raw HTML, no remote images); an image
  is fetched with the token and shown from a blob URL; any other file says it is listed but not shown. It opens on the
  newest file that can be shown, reads the list every three seconds while the tab is open, and says what to do when
  the folder is empty. No coral. The tab bar now scrolls sideways under 400px instead of wrapping, because seven
  labels overlapped, and the open tab is scrolled into view.
- **Terminal tab (CUT-96, 2026-10-16).** A project page has a **Terminal** tab after Overview: the agent's real
  tmux session in xterm.js, inside an `inverse-surface` panel (colours from the `--md-sys-*` roles, no raw hex), with
  a status line ("Connected to the agent's tmux session") and, below it, a row of 44px keys for a phone keyboard
  (Esc, Tab, Ctrl-C, the arrows, Enter). Under 640px the panel was 52dvh with room left above the fixed bottom
  nav; CUT-98 replaced that with a screen sized to the space left (see the entry at the end). With no session running it says so and offers **Start the agent session**; a lost connection says the agent
  is still running in tmux and offers **Reconnect**; a rejected sign-in offers nothing to retry. It opens only when
  the tab is shown, and never starts a session by itself. No coral: nothing here needs a decision from you.
- **Phone layout and sign-in again (CUT-98, CUT-112, 2026-10-17).** On a phone the Terminal tab hides the project's path and
  tightens the header, and the terminal is sized in script to what is left between the header and the bottom bar
  (`lib/terminallayout.ts`, from `visualViewport` so a keyboard shrinks it), with the key row (44px keys, two rows at
  390px) right under it instead of beneath the bar; at 844px tall the screen is 26 rows. Buttons are 44px tall under
  640px. The browser tab's title carries the Needs you count, `(2) cuttlefish-crew`. When the daemon answers 401 (a restart,
  a session past its idle time) a modal **Sign in again** dialog asks for the password, or the new token on loopback, with
  **Disconnect** as the way out; the page behind keeps its place and a terminal that closed reconnects after the sign-in.
  The Projects screen no longer says "No projects yet" while the daemon is unreachable. Neutral surfaces, no coral in
  the dialog. How a real on-screen keyboard resizes the terminal is checked on a phone, not in the emulator.

- **Top bar, account menu and Settings tab (CUT-98, 2026-10-17).** From a real phone pass: the bottom bar held five
  destinations plus the theme and sign-out buttons, and a project page had seven tabs, three of which shared a name with a
  destination. Now a top bar (title left, a round account button right, the mark on a phone) holds a menu: Theme (match
  system, light, dark), Sprite gallery and Disconnect. It is not sticky, because a pinned bar would cover the terminal when
  the keyboard scrolls it to the top of the page. The rail and bottom bar list only places that span every project: Needs you,
  Projects, **Role library** and **Shared secrets**. A project has Overview, Terminal, Needs you, Artifacts and **Settings**;
  Settings has Team, Permissions and Secrets as a row of pills (`Tabs` `variant="segmented"`) and reopens the section you
  were last in. Every old link (`/team`, `/permissions`, `/secrets`) still opens its section and `/settings` opens Team. Five
  tabs fit a 390px phone exactly; narrower scrolls. Trade-off: the top bar costs 56px of terminal height on a phone.

- **Pairing and Devices (CUT-120, 2026-10-17).** The account menu has **Devices** (`#/devices`). It shows: Pair a device (an
  address box, a note when the address is loopback because a phone cannot open it, and a button; then a QR code, the link, a
  five-minute countdown, Copy link and Cancel), **Waiting for you** (a device's name, a large six-digit code, Confirm and
  Reject; coral edge because it needs a decision), **Paired devices** (name, last used, an "Allowed to" select of View, Approve,
  Terminal and Admin with a line on what each means, and Remove with an inline "Sign out at once?"), and **Removed**. A page opened
  from a `#pair=` link shows **Pair this device** (a name box, Pair, then the same six digits large, "Confirm on the PC only if its
  screen shows this same code", and Cancel). A paired browser has no Connect screen and no token; if the owner removes it, it
  returns to Connect with "This device was removed on the PC." A refusal by tier is the daemon's own sentence wherever a save or
  an action would have said "check that the daemon is still running", and the terminal says it with a Reconnect button for once the
  owner raises the tier. Neutral surfaces; coral only on Waiting for you. Not in the mockups.
- **Needs you for a Claude Code session in tmux (CUT-127, 2026-10-17).** The Agent session card's sentence follows where that
  session's questions go: "appear under Needs you, and in the session's terminal too. Answer in either place" (started with the
  hooks), "started in Auto mode, so it never asks and nothing will appear under Needs you. Stop it and start it again after
  changing the mode", or the old "its own terminal" for Codex, kopicode and sessions started before. A card from such a session
  reads "Claude Code wants to run a command that isn't on the list" (or "wants to use WebFetch"), says "Paused until you answer
  here or in the terminal", and ends in the log as "Answered in the terminal", "No answer in time: the terminal still asks" (it
  denied nothing), "Ended: the agent session was stopped" or "Ended: the agent session ended". Coral as for every Needs you card.
- **Fewer steps to sign in and a right address to pair (CUT-133, 2026-10-18).** A dashboard the daemon serves goes straight to the
  password or token, with no Base URL step; a failed check, or a notice to read, still shows the address step. Under Devices, when
  the daemon was started with `--tailscale-https` it offers its https page as the address a pairing link opens (`page_url` in
  `/api/relay`), over the PC's own address or one left in storage, until the person types their own.
- **A device below Approve still sees what it is (CUT-132, 2026-10-18).** A paired device cannot list devices below Approve, so its
  Devices screen shows one card, "This browser", with its name and tier, what that tier allows, and that the list needs Approve.
  It used to stay on "Loading…". A pairing link that was already used or has expired says so on the Pair screen, in place of the
  daemon's bare "protocol error".
- **Pairing says when an address cannot work (2026-10-18).** The Devices screen's pairing card shows a note under the address box
  when the address will not let a phone pair: this PC's own address ("a phone cannot open this address"), or any plain-http one
  ("only allows pairing from an https address"). A page opened from a pairing link at an address with no WebCrypto shows that
  explanation instead of the Pair screen, with the Connect screen's password sign-in still there. Neutral text, no coral.

- **Portfolio card says what is going on (2026-10-19, CUT-156).** Each project card shows one state word per role (it printed it
  twice), a "N waiting for you" pill when requests of that project are open (from the same list the Needs-you badge polls; a failure
  there never hides the projects), and a line "Active 12 s ago · 1,083,519 tokens" (or "Last active …" when nothing is running). The
  time comes from `last_event_at` on the project summary, the last event of the last team's journal; the tokens are the sum of the
  roles' reported usage, so a Codex project shows none until a round ends (CUT-157 words that on the role card). No task text: the
  journal's task event begins with the role's own prompt.
- **A role's token line says when there is no figure yet (2026-10-19, CUT-157).** Codex and Claude Code hand their token count back
  when a round ends, so a running role on either read "0 tokens / 1,500,000" for minutes, as if nothing had been used. The role card
  now says "Tokens: not reported until the round ends (limit 1,500,000, checked then)" until the first figure arrives, and a line
  with a limit adds "limit checked when a round ends". kopicode and a role with a figure show the numbers as before.
- **Codex shows tokens while it works (CUT-102).** Codex over `app-server` reports usage after every model call, so a Codex role's card
  reads "Tokens: none reported yet; the figure updates as <role> works" until the first call finishes, then the running figure (seen
  live: 16,557, then 33,457 within half a minute). The daemon journals it as `RoundUsageUpdated` (first figure, then at most one every
  5 s, only when it changed); the activity log hides those rows. Claude Code still reads "not reported until the round ends".
- **Phone view, second pass (CUT-98).** Measured in Chromium at 390 px with touch against a real daemon and real Codex requests: no screen
  scrolls sideways. Below 640 px every button is 44 px high (the small Stop, Remove and Always-allow buttons were 32), the project card's
  name and the Settings sub-tabs are 44 px tall, and the project tab bar fits all five tabs with the Needs-you badge showing (it clipped
  Settings by 23 px). A permission card's Allow once, Deny and Always allow work by touch. Not done: a real phone (Safari, Android), the
  Terminal on a phone keyboard, and a push or badge outside the app.

- **A long folder path on the portfolio card loses its start (2026-10-19, CUT-160).** The path is cut with the ellipsis on the
  left ("…e/jianlee/projects/scratch-testing/ledger") so the project's own folder name, the part that tells two cards apart, is
  always visible; the full path is the card's tooltip. Plain CSS (`direction: rtl` on the line, `<bdi>` around the text); the
  project page still shows the whole path.
