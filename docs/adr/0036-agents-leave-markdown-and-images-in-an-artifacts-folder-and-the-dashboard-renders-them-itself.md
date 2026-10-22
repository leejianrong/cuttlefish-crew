# ADR-0036: Agents leave markdown and images in an artifacts folder, and the dashboard renders them itself

- Status: Accepted
- Date: 2026-10-16
- Deciders: Jian

## Context

ADR-0032 has agents show visuals by writing files to `.cuttlefish/artifacts/`, with no new protocol. The first plan
rendered agent HTML in a sandboxed iframe. That needs a sandbox without `allow-same-origin`, a CSP, and a way to fetch
the file with the token (an iframe cannot send a header), and it still lets agent code run next to an authenticated app.
Claude's own artifacts avoid it by serving from a separate origin.

## Decision

**Start with data, not code. The folder holds markdown and images, and the dashboard draws them itself.**

- `GET /api/projects/{id}/artifacts` lists the folder (newest first, at most 200, four levels deep, no hidden entries).
  `GET /api/projects/{id}/artifacts/file?path=` serves one markdown or image file with `nosniff` and
  `Content-Security-Policy: sandbox; default-src 'none'`. Other kinds are listed and answer 415; over a limit, 413.
- A path is resolved first and refused unless it is still inside the folder, so `..` and a symlink out are 404 and
  never listed.
- The dashboard parses markdown into a tree and renders the tree with Svelte, never as an HTML string. Raw HTML stays
  text, links keep only http, https, mailto and anchors, and an image is a base64 raster data URI or a file in the
  folder. Images are fetched with the token and shown from a blob URL, so an SVG never runs as a document.
- Agents are told in the task text of every round, and, for a Claude Code session in tmux, with
  `--append-system-prompt`. Codex and kopicode in tmux are not told yet; neither has a launch flag we checked.

## Update (CUT-175, 2026-10-22): video and HTML designs

ADR-0044 decided the canvas shows video and interactive HTML, so this ADR's "an iframe is not the plan" is superseded, with
the problems it listed answered one by one:

- **The file is still only data on the wire.** `.html` is served as `text/plain` with the same `nosniff` and `sandbox` headers
  and is fetched with the token; the dashboard hands it to a frame as `srcdoc`, so the frame has no URL on the daemon and
  there is no token in a URL. `.mp4` and `.webm` are served like images (100 MB cap) and played from a blob URL.
- **The frame has no authority.** `sandbox="allow-scripts"` only: no `allow-same-origin` (so an opaque origin: it cannot
  read the dashboard's storage or token), no navigation, forms, popups or downloads. A `Content-Security-Policy` meta tag
  is put before the file and allows no network, no frames, no form submission and nothing loaded from outside the file
  (`default-src 'none'`; inline script and style, and `data:` or `blob:` images, media and fonts, work).
- **Verified in a browser (Chromium):** a design that tried `fetch` to the daemon, `localStorage`, `document.cookie`,
  `parent.document`, `top.location`, and an external image got a SecurityError or a block for each, and reported
  `origin: null`.
- **Cost:** a design that loads a library or font from a CDN shows without it. A same-origin frame, or a second origin
  for real apps (CUT-115), is still not in scope.

## Consequences

- No sandbox to get wrong, no second origin, no token in a URL. It works the same for all three backends.
- No charts or interactivity yet. Rendered block types (CUT-114) and HTML as a link on a second origin (CUT-115) come
  later; an iframe is not the plan for either.
- The note is added to every round's task, so a round costs a few more prompt tokens.
- The list is polled, not pushed.
