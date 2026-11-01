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

## Charts, tables, diffs and checklists in markdown

A `.md` file can hold five fenced blocks that the dashboard draws instead of showing as code. They follow the theme (light
and dark) and fit a phone. Nothing in them becomes HTML. A block that does not parse shows as plain code, so a mistake
never breaks the page.

| Fence | Content | Drawn as |
|---|---|---|
| `chart` | JSON: `type` (`bar` or `line`), `title`, `labels`, and `series` of `{name, values}` (or `values` for one series) | an inline SVG chart with a legend |
| `table` | CSV, header on the first line, at least two columns | a table |
| `diff` | a unified diff | lines coloured as added, removed or header |
| `checklist` | `- [x] done` and `- [ ] to do`, one per line | a read-only checklist |
| `mermaid` | a flowchart: `flowchart TD` or `LR` (or `graph`), then `A[text] -->|label| B{text}` lines | an inline SVG flowchart |

Limits: 60 labels and 6 series per chart, 500 lines per block, numbers only in a chart.

`mermaid` is a small subset written for the dashboard, not the Mermaid library: only flowcharts (`flowchart` or `graph`,
direction `TD`, `TB` or `LR`), nodes as `A[box]`, `A(round)` or `A{decision}`, edges `-->`, `---`, `-.->` and `==>` with an
optional `|label|`, at most 40 nodes and 80 edges. Anything else (a sequence or class diagram, `subgraph`, `style`, other
node shapes) shows as code, so draw those as an SVG file instead. See ADR-0056.

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

## Present mode: a walk-through in order

To walk you through several files, an agent writes `.cuttlefish/artifacts/present.json`, at the top of the artifacts
folder and with exactly that name:

```json
{"title": "Checkout redesign", "items": [
  {"path": "slides/overview.html", "say": "Here is the plan in four slides."},
  {"path": "wireframe/cart.svg", "say": "The cart, with the new total row."}
]}
```

When the file exists, the Artifacts tab and the chat canvas show a **Present** button. It opens a full-screen view with
one item at a time and its `say` line underneath (as plain text, never HTML).

- Next and Back are buttons, the right and left arrow keys, Page Down and Page Up, and Space steps forward. Home and End
  jump to the first and last item, and the list jumps to any item.
- Escape, the Close button, or leaving full screen ends it. While an HTML design has focus, keys go to it and not to
  the dashboard, so click outside the frame to use the arrow keys, or use the buttons.
- The file is read each time Present is opened. At most 50 items and 1,000 characters of `say` are used. An item whose
  file is missing or cannot be shown is skipped and counted at the bottom; a file that is not valid JSON, or has no
  `items` list, shows a one-line message instead.

## Hear the walk-through

When the browser has speech synthesis, a **Play** button sits beside Next. It reads the current item's `say` line aloud
(or the file name if there is none), moves to the next item when the line ends, and stops after the last one. **Pause**
and closing silence it at once; Back, Next or the list while it plays reads the new item instead.

The voice is the browser's own, so it differs between a laptop and a phone. The text never leaves the page: cuttlefish
makes no model call to narrate, and no audio is made, sent or stored anywhere, the relay included. There is no separate
tool for an agent to call: the `say` lines are the narration, so write them as you would say them. A browser with no
speech synthesis shows no Play button.

Not here yet: choosing a voice, and HTML that needs the network.
