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
  summary column stacks below the form under 900px; Ask first's description says plainly that
  nothing asks yet (ADR-0025). Registering opens the new project's page.
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
- The nav rail lists only screens that exist (`frontend/src/lib/nav.ts`); Fleet and Roles
  join it with V4-H and V4-F. The rail becomes a bottom bar under 640px. A theme button
  cycles system, light, dark and is remembered per browser.

- Stored role prompts open with the instruction, not "You are the builder.", because
  cuttlefish prepends "You are {name}." to every task. The prompt shown in
  `4-roles-and-teams.dc.html` should drop that opener when the screen is built.
- The builder prompt's "ask one specific question" line is not in the shipped prompt:
  only kopicode can ask today (V4-H), so it says to state the assumption and take the
  most conservative reading. Restore the question once asking works on every backend.
- Project mode is `ask-first`, `standard` or `auto`; a role's `access` is null (inherit),
  one of those, or `read-only`. Read-only blocks edits on Claude Code and Codex only, so the
  UI must not call it "can't edit" without the per-backend caveat. Ask first does not ask
  until V4-H; do not show an "answer" affordance for it before then.
- Auto is answered by cuttlefish (`ConsentPolicy(auto=True)`), so it is available on kopicode
  now; it needs the `serve` transport.
- `make` commands are on by default (not in the Permissions mockup).

## Known gaps in the mockup

- Static: no working interactions, no empty or error states, no dark theme, no phone
  layout (the shipped dashboard must work at phone width).
- The project page in screen 2 uses a Full crew team while screen 1 selects Builder +
  reviewer; they are different projects.
- No screenshots are checked in; the canvas is the visual reference.
