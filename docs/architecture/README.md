# Architecture diagrams

The C4 views of cuttlefish-crew are drawn from one file, `model.mjs`, so a box means the same thing in every
view. The page that shows them is [`docs-site/architecture-diagrams.md`](../../docs-site/architecture-diagrams.md)
(also built into the docs site); the SVGs it embeds are in `docs-site/assets/architecture/`.

```
make diagrams        # npm install (elkjs only), then node render.mjs
node render.mjs c4-2-containers   # one view, from this folder
```

- **Edit the model, never the SVGs.** Commit the model and the regenerated SVGs together. Nothing checks that they
  match, so run `make diagrams` before you push a change to either.
- **Node kinds** are `person`, `system`, `ext`, `container`, `db`, `ext-container`, `ext-db`, `component`, and the
  groups `boundary` and `deploy`. An edge is `[from, to, "what it does", "how"]`.
- **Layout is automatic** ([ELK](https://eclipse.dev/elk/), layered, orthogonal edges). To fix a crossing, reorder
  nodes or edges in the model, or change a view's `dir` (`DOWN` or `RIGHT`). A group that holds nodes which all
  connect to something outside it can crash ELK if it is laid out separately, so groups share the root layout.
- **Text is wrapped by estimate**, not measured by a browser, so a very long word or description can look tight. Shorten
  it before widening a box.
- **Light and dark** are inside each SVG (`prefers-color-scheme`), and each SVG paints its own background.
- The model names the module behind each box. When code moves, search the model for the old path.
