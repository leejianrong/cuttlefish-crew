# ADR-0023: A team with nothing declared gets built-in dev presets, not no shell

Status: accepted (docs/SLICES.md V4-A)

## Context

The default allow list was empty, so a freshly registered team could edit files but not
run its own tests, linter or `git status`. The only way to fix that was to type commands
into a textarea. Operator feedback (2026-10-06): too restrictive; agents should be able to
do everyday software work out of the box.

## Decision

- `cuttlefish.delegate.presets` holds named presets of plain argv prefixes. The default
  set is inspect, git-read, git-save, python, javascript and make; go-rust and containers
  exist but are opt-in. A declaration (`--allow`, a project's allow list) is added on top;
  nothing replaces the presets yet. A read-only role is a later slice (V4-B/V4-C).
- `resolve_allow` is applied **inside the side-effecting delegation task** and where the
  policy is journaled, never in a task argument, so recorded arguments stay the raw
  declaration and an in-flight run still replays. A preset change between a crash and a
  resume therefore applies on resume; accepted, the presets only grow.
- `cuttlefish.delegate.never_allowed` is the one shared never-allowed list: privilege
  escalation, a forced `git push`, a download piped into a shell, plus flags that make
  `find`, `rg` and `git commit` write or execute (`-delete`, `-exec`, `--pre`,
  `--no-verify`). It is checked before any allow rule and refuses an allow entry that is
  itself never allowed. Writes and path arguments outside the root stay with
  `ConsentPolicy`. It holds in every permission mode that follows.
- `git commit -m '<message>'` (and `-am`) is the one quote-aware rule, on only when
  `git commit` is allowed. Single quotes hold anything but a quote or newline; double
  quotes exclude `$`, backtick, backslash and `!`; no other flag rides along.
- Claude Code gets the presets as `--allowedTools` plus deny patterns for the
  never-allowed prefixes. Codex still only switches `--sandbox` to `workspace-write`.

## Consequences

- Approving `uv run pytest`, `npm run build` or `make ci` approves running repository
  code as this user. That is ADR-0002's trust model, unchanged, and not containment.
- A model that chains commands (`cd x && make test`) is still denied by the plain
  word-list rule; Auto mode (V4-C, kopicode#164) is the answer to that, not looser
  matching here.
- The Claude Code mapping does not give `find`/`rg` the unsafe-flag protection kopicode
  gets (known-gaps.md).
