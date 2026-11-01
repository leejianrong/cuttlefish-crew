---
icon: lucide/presentation
---

# Let an agent present its work

An agent can show you more than text: a slide deck, a wireframe, an animation, a clickable demo or a dashboard. It
leaves a file in `.cuttlefish/artifacts/` and the project page shows it on the canvas. It works the same for Claude
Code, Codex and kopicode, because the only thing an agent needs is to read a file and write one.

## How an agent learns how

Before a round, and when a session starts, cuttlefish writes `.cuttlefish/present/SKILL.md` and one working example per
kind into the project's root. Every round's task (and a Claude Code session's system prompt) already carries a short
note about the artifacts folder; it now ends with a pointer to that file. Your own `AGENTS.md` and `CLAUDE.md` are not
touched, and `.cuttlefish/` stays out of `git status`.

The file is rewritten if it differs, so an edit to it does not last. A Codex or kopicode session in tmux is not given the
note (neither has a launch flag for it), so name the file in your message if you want one to present.

## The kinds

| Kind | File under `.cuttlefish/artifacts/` |
|---|---|
| doc | `doc/<slug>.md` |
| slides | `slides/<slug>.html` |
| wireframe | `wireframe/<slug>.html` or `.svg` |
| animation | `animation/<slug>.html` |
| demo | `demo/<slug>.html` |
| dashboard | `dashboard/<slug>.html` |

An HTML file must be self-contained: inline CSS and JavaScript, no network and no files from outside. It runs in a
frame with no access to the dashboard, so a `fetch` or a look at `window.parent` fails. See ADR-0056 and ADR-0036.

Not here yet: narration, a present mode, and HTML that needs the network.
