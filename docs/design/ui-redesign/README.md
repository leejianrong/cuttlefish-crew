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
- **Coral (the tertiary role) means "needs you", and only that.** Used for the Fleet
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

## Known gaps in the mockup

- Static: no working interactions, no empty or error states, no dark theme, no phone
  layout (the shipped dashboard must work at phone width).
- The project page in screen 2 uses a Full crew team while screen 1 selects Builder +
  reviewer; they are different projects.
- No screenshots are checked in; the canvas is the visual reference.
