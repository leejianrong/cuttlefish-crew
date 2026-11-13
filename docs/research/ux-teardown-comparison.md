# UX teardown: herdr, Paseo, Claude Squad and Orca against our screens (CUT-228, 2026-11-12)

Per-product notes: [herdr](herdr.md), [Paseo](paseo.md), [Claude Squad](claude-squad.md), [Orca](orca.md). Earlier passes:
`fleet-manager-landscape.md` and `agent-session-ux-comparison.md`. **No competitor was run.** Their screens here are the
vendors' own marketing images, so they show each product at its best. Ours are real captures of the current `main` dashboard.

## Our side: how these were taken

`scripts/seed_fleet.py seed` (100 fake projects, no agent launched) into a fresh scratch HOME, then `cuttlefish serve` with a
freshly generated secrets key and the built dashboard, driven by Playwright (Chromium) at 1440x900 light, 1440x900 dark and
390x844 light (`prefers-color-scheme` set per run). Run from outside the repo; the server and scratch files were removed after.

| Capture | File |
|---|---|
| Projects, desktop light | `assets/teardown/cuttlefish-overview-desktop-light.png` |
| Projects, desktop dark | `assets/teardown/cuttlefish-overview-desktop-dark.png` |
| Projects, 390 px | `assets/teardown/cuttlefish-overview-phone-390.png` |
| One project, desktop light / dark / 390 px | `assets/teardown/cuttlefish-project-{desktop-light,desktop-dark,phone-390}.png` |
| Needs you (empty), desktop light / dark / 390 px | `assets/teardown/cuttlefish-needsyou-{desktop-light,desktop-dark,phone-390}.png` |

**What the seeded fleet cannot show:** a live agent mid-task, a pending permission (so the Needs-you badge and a request card),
the Chat and Terminal tabs with a real session. The seed launches nothing. Those screens are not in this comparison and the
Needs-you captures are the empty state. Nothing here says how our Chat tab looks beside Paseo's timeline.

## What I saw on our screens

1. **Projects, desktop.** The first thing the eye lands on is the "Stop all agents" button, the most saturated element on the page
   (red-tinted in light, solid dark red in dark), sitting above the state filters. Below it: state chips (Waiting 15, Stuck 15,
   Working 30, Done 30, Idle 10), tag chips cut off at the right edge, a "Group by" select, then three-column cards. A card is
   name, a long filesystem path, a state pill with one line of why ("builder is waiting for your review"), tags, two sprites,
   last-active and token count, then Open / Pin to top / Tags / Remove. Waiting projects sort first (CUT-189).
2. **Cards are tall and text-heavy.** Three per row at 1440 px, about 390 px tall each, so a 100-project fleet is a long scroll.
   The path takes a full row on every card; the state and the sprite row carry the information.
3. **Phone.** The bottom nav works and fits four items. But the fold shows the title, the address, Add project, Stop all agents,
   two rows of filter chips (clipped: "Do...", "stack:rust"), the Group-by control, and only the top of one card. The first
   project is below a third of the screen. "Stop all agents" is the largest thing on a 390 px screen.
4. **Project page.** A back link, the name, a path that wraps over five lines on a phone, "Stop everything here", then six tabs
   (Overview, Chat, Terminal, Needs you, Artifacts, Settings). The tab strip fits 390 px. The body leads with "Give the builder a
   goal", suggestion chips, a goal box, a backend choice, Autopilot, limits. The page does not say, above the fold, what the agent
   is doing now. In the seeded state that is correct (nothing runs), but there is no status strip on this page to carry it later.
5. **Needs you.** Four lines of explanation before the empty state. At 390 px the explanation takes about a third of the screen.
   The nav badge exists in code (`App.svelte`, `Shell.svelte`) but could not be seen with no pending requests.
6. **Light and dark** both hold up: contrast is fine, state pills are legible in both, the page title in the top bar stays
   "Projects" on a project page in both.

## Side by side

| Question | herdr | Paseo | Claude Squad | Ours (seen above) |
|---|---|---|---|---|
| What is the unit of a row? | An agent, grouped by status (phone Inbox: Needs you / Done / Working / Idle) | A workspace (worktree) under a project, with `+/-` and PR state | An instance with `+/-` and a dot | A project, with its roles as sprites |
| Is "needs you" a section? | Yes, first group, orange pill, "now" | Not visible in the hero shot | A dot colour only | A filter chip, a sort order and a separate page, but on the Projects screen it is just one pill among many |
| Density on a phone | About 8 agents on one screen | Compact workspace list; full app | n/a | One card, below four controls |
| Diff / PR in the main view | Files and Changes panels (site shot) | Right-hand diff panel, View PR button | Diff tab, `s` submits PR | None on the Projects screen; Artifacts tab (not seen) |
| Destructive control placement | Not shown | Not shown | `D` key, no button | Biggest button on the screen |
| Answering an agent from the phone | Type into its real terminal; key bar for Esc/Ctrl/Tab | Composer on the session; a shield icon for permissions | n/a | A Needs-you card with approve/deny (code; not captured) |
| Dark mode | Dark by default | Dark in shots | Terminal | Both, both fine |
| Sense of life | Spinner on a working agent | Streaming timeline | Live Preview | Pixel sprites (builder "blocked", reviewer "z") |

## Findings that follow (ranked)

Filed as CUT-282 to CUT-288, in that order (findings 1 to 7).

1. **Phone Projects screen spends its fold on controls.** Collapse the filter chips and Group-by behind one "Filter" button below
   about 600 px, keep Add project, show the first card fully. Effort small, impact high.
2. **De-emphasise "Stop all agents".** It is a real safety control (the kill switch, CUT-186), so keep it reachable, but make it a
   quiet outlined button in the page header, red only on hover, press, or confirm. Effort small.
3. **Add a "Needs you" lead group on Projects**, as herdr's Inbox does: when any project has a pending request or is waiting,
   show that group first with the reason, then the rest. The data is already in the sort (CUT-189). Effort medium.
4. **Shorten the card.** Drop the path to the folder name (full path on hover and on the project page), wrap long paths on the
   project page. At 3 per row that is roughly a third more projects per screen. Effort small.
5. **A one-line status strip at the top of a project page** (what is running, last action, tokens, time), above "Give the builder a
   goal", so a person returning to a running project sees state first. Paseo's header shows branch, PR and `+/-` there. Needs a
   running agent to design properly; effort medium.
6. **Review surface: show a diff stat and PR link on the card or strip** for any project with a branch, as Paseo and Claude Squad
   do. Check what the Artifacts tab and git integration already expose first (code beats prose). Effort medium to large.
7. **Retake with a live team.** Put the real Chat, Terminal and Needs-you screens (a pending request, the badge) beside herdr's
   Inbox and Paseo's session view. Done 2026-11-13 (CUT-288), see "Live retake" below.

Two competitive facts that change earlier notes, for the front door (E28) and the README:

- herdr **does** have iOS, iPad and Android apps (direct SSH, no account, no relay); `fleet-manager-landscape.md` said "None in
  docs". Our claim of E2E encryption and a hosted relay is not matched there, but their phone path has fewer moving parts.
- Paseo is **Apache-2.0**, not AGPL; it also has a hosted Hub ($15 per seat per month, free self-hosted) that starts agents from
  GitHub, Slack and Discord. That is the same job as the "Devin in Slack" flow we listed as worth copying.

## Live retake (CUT-288, 2026-11-13)

Finding 7 above, done. A scratch `cuttlefish serve` (scratch `HOME`, fresh secrets key, port 8791, the built dashboard) with one
project, a tiny git repo, and **one real Claude Code session in tmux on Jian's subscription** (`CLAUDE_CONFIG_DIR` passthrough,
as in `agent_docs/exploratory-testing.md`; no kopicode, no OpenRouter, no API key). The session wrote a file, then asked for
WebFetch, which raised a real Needs-you request through the hook (ADR-0040). Three Chat messages, a few thousand tokens. Driven
by Playwright at 1440x900 light, 1440x900 dark and 390x844; no console errors and no horizontal overflow in any of them.
Captures are in `assets/teardown-live/` (`cuttlefish-live-<screen>-<size>.png`: projects, overview, chat, terminal, needsyou).
Competitor images are the same vendor shots as above.

**The dashboard has moved since the first pass**, so some findings are already fixed: Projects now leads with a "Needs you" group
(a compact row with the request text; finding 3), "Stop all agents" is a quiet outlined button (finding 2), and the nav badge
shows the pending count on the Needs you item and on the project's tab. Those work as designed with a real request.

What the live screens show:

1. **Needs you, with a real request, is good.** One card: kind pill, backend, project, "Paused until you answer here or in the
   terminal", the tool and its arguments in a code block, Allow once / Deny, a countdown ("Denies on its own in 8:43"). It is
   the same card in Chat and Overview, and at 390 px it fits with the buttons above the fold. Compared with herdr's Inbox (one
   orange "Needs you" pill per row, answer by opening the terminal) ours answers in place; compared with Paseo (a red shield
   icon, answers in the composer) ours is more explicit.
2. **The card has a wrong line.** A WebFetch request says "This command uses shell syntax (a redirect, variable or substitution),
   or has a part that is not on the list, so it can only be allowed once." It is not a command (CUT-307).
3. **Chat reads well but the composer is not on screen.** The thread is a tidy chat: "You" bubbles, one collapsed row per tool
   burst ("Edited 1 file", "Ran 1 command, 1 failed", "Looked up 1 page"), the agent's reply as plain text. But at 1440x900 the
   page scrolls, not the thread, so the composer sits below the fold, and the right column ("Nothing to show yet") is a third of
   the width. Paseo's composer is pinned and carries model, effort and a permission shield; ours has no model or mode on it, and
   "Edited 1 file" has no `+N` (Orca: "Edited CHANGELOG.md +6") (CUT-308, CUT-309). On a phone the composer is reachable but
   two paragraphs of help sit under it while a request is pending; the Send button is correctly disabled with the reason.
4. **Terminal is the strongest screen.** The real Claude Code TUI in the browser, a "1 file changed" diff panel beside it, and on
   a phone a key bar (Esc, Tab, Ctrl-C, arrows, Enter), as herdr and Paseo have. The cost: at 390 px the Agent/Shell and
   Match app/Dark/Light toggles take about 300 px before the terminal starts (CUT-309).
5. **Project page header.** "Stop everything here" is still the loudest thing on the project page (pink in light, solid red in
   dark), and the path takes a full row; the top bar says "Projects" on a project page. The Overview does lead with the
   waiting request and a "Claude Code running, started 06:47 PM" line, which is the beginning of finding 5's status strip, but
   it has no last action or tokens (CUT-308).
6. **"While you were away: Since 06:50 PM yesterday"** appeared for a session started minutes earlier, on a browser that had
   never opened the page. Probably the first-visit default; not carded, worth a look when the card is next touched.
7. **Light and dark** both hold; the "You" bubble is a saturated cyan in light and loud against everything else.

Not a product gap: the user's own `PreToolUse` hook (a `secrets-guard.py` addressed through `$HOME`) failed under the scratch
`HOME` and denied the Bash call, so the Needs-you request was raised with WebFetch instead. The chat showed that failure
honestly ("Ran 1 command, 1 failed", the agent's explanation).

Cleanup: the session, tmux server (own socket) and daemon were stopped by pid/socket; the Claude project folder for the
scratch path under `~/.claude/projects` was removed. Claude also wrote `.claude.json.backup.*` files under the real config
directory, left in place (Jian's, newest ones to keep per the notes above).

New cards: CUT-307 (wrong shell-syntax line), CUT-308 (project-page Stop, composer below the fold, bubble colour), CUT-309
(model and mode on the composer, `+N` on edits, phone Terminal toggles).

## Orca against our screens (added 2026-11-12)

Orca (`orca.md`) is the largest product here and the only one whose marketing shots show a coordinator agent starting workers
on other agents. Its images are the README's own, so best case again. Ours are the same retaken `main` captures above
(`assets/teardown/cuttlefish-*`); nothing was retaken.

What the Orca hero (`assets/orca/orca-readme-hero.jpg`) shows, and how ours compares:

| Question | Orca | Ours (seen above) |
|---|---|---|
| What is the unit of a row? | A worktree, with its agents nested under it as rows: glyph, name, model, age (`opus-5.5 49m`) | A project card with sprites for roles; no model or age |
| Tree or cards | One narrow sidebar tree: host, project, worktree, agent. About 10 agents visible on a laptop screen | Three tall cards per row; about one card on a phone fold |
| Where is "needs you" | An amber glyph on the agent row, and a Needs You column in the experimental board | A chip, a sort order and a separate page; one pill among many on Projects |
| Cost and limits on screen | A status bar with a limit bar and reset time per provider (`42% 2h 48m`) | None |
| Review | The agent's chat shows "Edited CHANGELOG.md +6"; diff and PR in the same window | Nothing on Projects; PR button on the project page |
| The phone | The same chat in the corner of the desktop hero, with a reply box ("Ship it. Open the PR.") | A phone capture, empty-state only |
| Permission state | The composer says "Full access" in amber | A mode setting in a tab; never on the composer |
| Density | Compact, dark, one font size | Roomy; the path takes a full row |

Findings (each has a card; the full list, with effort and fit, is the copy list in `orca.md`):

1. **Nest the agents, show model and age on each row** (CUT-298; shortening the card is CUT-285).
2. **Put a usage and reset line on screen** (CUT-292). Orca's status bar is the quietest of its features and the one a
   subscription user checks most.
3. **Show what an agent just changed in the row or the chat** ("Edited X +6") and give it a diff (CUT-289, CUT-287).
4. **Show the permission mode on the composer**, as Orca does with "Full access". Theirs is a risk label; ours would say
   "Ask first", "Standard" or "Auto". Not carded separately: it belongs in the chat-rework screens (E20) and needs a live team to
   design (CUT-288).
5. **Hero with the phone overlaid** (CUT-302).

The hero's model names (`gpt-6-astra`, `grok-4.7`) are mock data. No video was found for Orca on its home page.

## Image provenance

All competitor images are the vendors' own, kept for internal comparison only, not for the docs site or README.

| File in `assets/teardown/` | Source URL |
|---|---|
| `paseo-hero-mockup.png` | https://paseo.sh/hero-mockup.png |
| `paseo-mobile-mockup.png` | https://paseo.sh/mobile-mockup.png |
| `herdr-mobile-inbox.webp` | https://herdr.dev/mobile/inbox.webp |
| `herdr-mobile-terminal.webp` | https://herdr.dev/mobile/terminal.webp |
| `herdr-mobile-foldable.webp` | https://herdr.dev/mobile/foldable.webp |
| `claude-squad-screenshot.png` | https://raw.githubusercontent.com/smtg-ai/claude-squad/main/assets/screenshot.png |
| `../orca/orca-readme-hero.jpg` | https://raw.githubusercontent.com/stablyai/orca/main/docs/assets/readme-hero.jpg |
| `../orca/orca-mobile-companion.jpg` | https://raw.githubusercontent.com/stablyai/orca/main/docs/assets/readme-features/mobile-companion.poster.jpg |
| `../orca/orca-orchestration.jpg` | https://raw.githubusercontent.com/stablyai/orca/main/docs/assets/readme-features/orchestration.poster.jpg |
| `cuttlefish-*.png` | our own capture, 2026-11-12 |
| `../teardown-live/cuttlefish-live-*.png` | our own capture of a live Claude Code session, 2026-11-13 |

No screenshots from Jian were available (`~/teardown-shots/` did not exist).
