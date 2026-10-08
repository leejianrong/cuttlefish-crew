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

