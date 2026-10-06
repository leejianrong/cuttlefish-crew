# ADR-0026: The daemon lists folders for the picker, inside configured roots only

Status: accepted (docs/SLICES.md V4-E)

## Context

Registering a project meant typing an absolute path into a text box. The dashboard cannot
browse the disk itself; the daemon, which runs on the operator's machine, can. A folder listing
behind an HTTP port is a filesystem browser, so what it may show has to be narrow and decided
on the server.

## Decision

- `GET /api/fs?path=` lists subfolders and `GET /api/fs/inspect?path=` describes one folder
  (git branch, whether it has uncommitted changes, last commit age, marker-file languages).
  Both sit behind the same token or session as every other `/api/` route.
- `cuttlefish.fleet.fs.FolderBrowser` answers only inside the **browse roots**
  (`cuttlefish serve --browse-root`, repeatable, default the home directory). A requested path
  is resolved first, with `..` and symlinks followed, and refused with 403 if it lands outside
  every root; a missing path or a file is 404.
- It lists **directories only**. Hidden entries (a leading dot) are not listed, and a hidden
  folder cannot be listed even by typing its path. A symlink is shown only when its target is
  also inside a root, so a link out of the tree is invisible rather than a way through it.
  A listing is capped (500) and says when it was truncated.
- `inspect` runs read-only `git` with a 3-second timeout and `GIT_OPTIONAL_LOCKS=0`, so it
  never takes the lock a running agent's git needs. Both routes run their blocking work under
  `asyncio.to_thread`.
- A daemon built without a `FolderBrowser` (tests, embedded use) answers 404 on both routes
  rather than browsing a default.
- The picker limits browsing, not registration: `projects add` and `POST /api/projects` still
  accept any path, so the CLI keeps working outside the roots.
- The add-project screen replaces the register form: pick a folder, a team template
  (ADR-0024) and a mode (ADR-0025), with name, backend, spend limits and extra commands under
  a collapsed "Advanced options". After adding, it opens the new project.

## Consequences

- In non-loopback mode (ADR-0011) an authenticated user can list the folder names under the
  roots from another machine. Names only, never contents, but an operator who exposes the
  daemon should set `--browse-root` to a workspaces folder, not leave it at the home directory.
- No "New folder" button yet; create the folder first.
- The path check runs at request time, so a symlink swapped after a listing is caught on the
  next request, not before it.
