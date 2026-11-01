# Present your work

Show a person what you made by writing a file into `.cuttlefish/artifacts/` in the project root. The dashboard
shows it next to the chat. Present when a picture or a walk-through says more than a paragraph: a plan, a design, a
finished feature, a status. Do it once per milestone, not after every edit, and tell the person in chat which file
to open. Steps: pick the kind, copy its example from `.cuttlefish/present/examples/`, replace the content, save it
under `.cuttlefish/artifacts/<kind>/<slug>.<ext>`.

## Kinds

| Kind | Path | Use it for |
|---|---|---|
| doc | `doc/<slug>.md` | plan, review, summary (markdown) |
| slides | `slides/<slug>.html` | a short walk-through, one idea per slide |
| wireframe | `wireframe/<slug>.html` or `.svg` | how a screen or layout will look |
| animation | `animation/<slug>.html` | how something flows or changes over time |
| demo | `demo/<slug>.html` | a mock of the feature you can click, with fake data |
| dashboard | `dashboard/<slug>.html` | numbers drawn as bars, lines or tiles |

`<slug>` is lowercase letters, digits and hyphens. An image (png, jpg, gif, webp, svg) or a video (mp4, webm) may
sit in any of these folders.

## Rules for an HTML file

The file runs in a locked frame: no network, no access to the app, no way to navigate away. So:

- One self-contained file. Inline `<style>` and `<script>`. No `<script src>`, no `<link>`, no CDN, no web font.
- No `fetch`, `XMLHttpRequest`, `WebSocket`, `localStorage`, `window.parent` or `window.top`. They fail.
- Images, audio and fonts only as `data:` URIs. Put the data the page draws inside the file (a JS array or JSON).
- Start with `<title>` (the name) and `<meta name="cuttlefish:artifact" content="KIND">`. Add
  `<meta name="description" content="one sentence">`.
- Make it readable at phone width (390 px) and on a dark or light background: set `color` and `background` yourself.
- Keep it small, under 200 KB. A slide deck has about 10 slides at most.

For a doc, start with a `# Title` line. Raw HTML inside markdown is not shown.

## Blocks in a doc

Four fenced blocks in a `.md` file are drawn, not shown as code. One that is malformed shows as plain code, so check
the JSON or the columns.

````
```chart
{"type": "bar", "title": "Tests per module", "labels": ["api", "cli", "core"],
 "series": [{"name": "unit", "values": [40, 12, 75]}, {"name": "integration", "values": [8, 3, 20]}]}
```
````

- `chart`: JSON. `type` is `bar` (default) or `line`. `labels` and every series' `values` have the same length (numbers
  only, at most 60 labels and 6 series). `"values": [..]` instead of `series` is one unnamed series.
- `table`: CSV, the first line is the header, at least two columns and one row. Quote a cell that holds a comma.
- `diff`: a unified diff; `+` lines are green, `-` red, `@@` headers coloured.
- `checklist`: one item per line, `- [x] done` or `- [ ] to do`. Read-only.

There is no diagram language yet: draw a flow as an SVG or HTML file instead.

## Walk the person through several files

To have the dashboard step through files in order, with a line of what you would say under each, write
`.cuttlefish/artifacts/present.json` (top level, exactly that name). The person gets a Present button. See
`.cuttlefish/present/examples/present.json`.

```json
{"title": "Checkout redesign", "items": [
  {"path": "slides/overview.html", "say": "Here is the plan in four slides."},
  {"path": "wireframe/cart.svg", "say": "The cart, with the new total row."}
]}
```

- `path` is relative to `.cuttlefish/artifacts/`; `say` is plain text, one or two sentences. `title` is optional.
- At most 50 items. An item whose file is missing or cannot be shown is skipped, so write the files first.
- Write it again when the walk-through changes; the dashboard reads it each time Present is opened.

## Check it

Read your file once as the person would. Nothing may depend on a file or address outside it. Say in chat what you
made and where it is.
