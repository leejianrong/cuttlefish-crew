# ADR-0056: Artifact kinds are folders of self-contained files, taught by one instruction file

- Status: Accepted
- Date: 2026-11-01
- Deciders: Jian

## Context

Epic E23 (docs/plans/2026-10-31-autonomous-portfolio-vision.md) wants any coding agent to present slides, a wireframe, an
animation or a demo, as Claude's artifacts do. ADR-0036 already decided how a file reaches the person: an agent writes to
`.cuttlefish/artifacts/`, and the dashboard fetches it with the token and draws it. Markdown, images and video are data;
`.html` runs in a frame with `sandbox="allow-scripts"` (an opaque origin) under a no-network CSP (CUT-175, ADR-0044). What
is missing is a shared vocabulary of kinds and a way every backend learns it. ADR-0003 and ADR-0005 forbid a new protocol
per backend, ADR-0042 forbids a model call by cuttlefish, and ADR-0004 forbids a parallel transcript.

## Decision

**A kind is a convention over files the canvas already renders. There is no new endpoint, wire format or renderer.**

### Kinds and formats

| Kind | Folder | File | Notes |
|---|---|---|---|
| `doc` | `doc/` | `<slug>.md` | Markdown as today (plans, reviews, summaries). |
| `slides` | `slides/` | `<slug>.html` | One file; each slide a `<section class="slide">`; own keys and buttons for next/previous. |
| `wireframe` | `wireframe/` | `<slug>.html` or `.svg` | Static layout in inline HTML/CSS or SVG. |
| `animation` | `animation/` | `<slug>.html` | CSS animation, SVG, or a small inline script on a canvas element. |
| `demo` | `demo/` | `<slug>.html` | A live-feeling mock of the app: inline script drives fake data. Not the real app. |
| `dashboard` | `dashboard/` | `<slug>.html` | Numbers embedded in the file as JSON, drawn with inline SVG or CSS. |

A recording (`.mp4`, `.webm`) or image may sit in any kind's folder. `<slug>` is lowercase letters, digits and hyphens.
A kind is the first path segment under `.cuttlefish/artifacts/`; a file outside a kind folder (such as `plan.md`, which
Autopilot reads) stays valid and is an unclassified artifact.

### Manifest

No sidecar file. The manifest is in the file, so one file is one artifact:

- HTML: `<title>` is the name, and `<meta name="cuttlefish:artifact" content="slides">` repeats the kind and
  `<meta name="description" content="...">` gives one sentence. Both are optional and the folder wins on a conflict.
- Markdown: the first `# heading` is the name. No front-matter, since the canvas would show it as text.

The listing (`GET /api/projects/{id}/artifacts`, unchanged) already returns path, kind and time. Showing a kind badge from
the first path segment, and reading the manifest, are dashboard work for a later card; nothing depends on it.

### Security (restated from ADR-0036 and ADR-0044, unchanged)

- Agent HTML is code. It is served as `text/plain` with `nosniff` and `Content-Security-Policy: sandbox; default-src 'none'`
  and shown only as `srcdoc` in a frame with `sandbox="allow-scripts"` and the dashboard's CSP meta first.
- So a file must be self-contained: inline CSS and JavaScript; images, media and fonts only as `data:` or `blob:`; no
  `fetch`, no CDN script or font, no link out. It cannot read the app's storage, token or parent, and cannot navigate.
- Paths are resolved and refused outside the folder; symlinks out are never listed.

### How each backend is told

One instruction file, `.cuttlefish/present/SKILL.md`, written by cuttlefish into the project's root with its examples
(`.cuttlefish/present/examples/<kind>/`). It is the same text for Claude Code, Codex and kopicode. It is found through the
note every round's task and every session's first prompt or system prompt already carries (`artifacts.AGENT_NOTE`), which
gains one sentence naming the file. cuttlefish does not edit the project's own `AGENTS.md` or `CLAUDE.md`, which belong to
the person, and invents nothing a backend must support: reading a file is something every agent does. A Claude Code skill
in `.claude/skills/` would reach one backend only, so it is not used. The file is derived content, rewritten when it
differs, and lives under `.cuttlefish/`, which is already kept out of `git status`.

## Deferred

- Text-to-speech narration: built (CUT-201) as a Play button that speaks the `say` lines with the browser's own speech
  synthesis. No tool, endpoint or audio file; a voice picker is still deferred. Present mode (CUT-199) shipped without speech: the agent writes
  `.cuttlefish/artifacts/present.json` (`{title, items: [{path, say}]}`), the listing gives that one top-level name the kind
  `manifest`, and the dashboard steps through it full screen. Still no new endpoint or renderer.
- A second origin for HTML that needs a network or libraries (CUT-115), and rendered block types in markdown (CUT-114).
- Dashboard UI for kind badges and manifest reading.
- Telling Codex and kopicode sessions in tmux (no launch flag); they find the file only when a task names it.

## Consequences

- A new kind is a folder name and a paragraph in the instruction file, not a release.
- A design that wants a library shows without it, as ADR-0036 already accepts.
- The instruction file costs a read by the agent, not tokens on every round beyond one sentence.
