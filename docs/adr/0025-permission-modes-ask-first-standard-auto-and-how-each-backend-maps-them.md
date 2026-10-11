# ADR-0025: Permission modes (Ask first, Standard, Auto) and how each backend maps them

Status: accepted (docs/SLICES.md V4-C)

## Context

Operators run coding agents in an auto mode on their own repos and want that here, but also
want a careful default and a read-only role. Each backend has different controls: kopicode
asks cuttlefish per command (ADR-0021), Claude Code takes allow and deny tool patterns,
Codex has one coarse sandbox dial (ADR-0018).

## Decision

- A **project mode** (`ask-first`, `standard`, `auto`; default `standard`; a nullable column
  read as standard) and a **role access** override (`None` inherits; a mode; or `read-only`).
  The effective access is role, else project, else standard (`cuttlefish.permissions`).
- It reaches the delegation task as `access`, **only when not standard**, so a default run's
  recorded arguments and an in-flight replay are unchanged. The daemon resolves it per role
  and persists the resolved value in `PersistedRole`, so a resume rebuilds the same team.
- The task turns it into what a backend needs: `read-only` is the inspection-only list,
  `ask-first` is an empty list, `standard` and `auto` are the presets plus declarations.
  `auto` and `read-only` also reach the backend as `mode`.
- **Auto is answered by cuttlefish, not by a kopicode mode.** cuttlefish already answers every
  `consent.request`, so `ConsentPolicy(auto=True)` allows any shell command that is not
  never-allowed, with no plain-word-list rule. kopicode#164 (a native `auto` consent mode)
  would move that decision into kopicode; nothing here depends on it landing. Auto needs the
  `serve` transport; the exact-match policy-file path raises rather than pretend.
- The never-allowed list now also refuses a write whose target leaves the root: `rm`,
  `mv`, `cp`, `touch`, `tee`, `dd of=` and `>`/`>>` redirects to an absolute, `~`, `..` or
  variable path. Best effort on text, not containment.
- **Mapping**
  - kopicode: Standard and Auto as above; Ask first denies every command (no prompts yet,
    V4-H turns these into requests); Read-only is the inspection-only list. kopicode has
    no way to refuse an in-root edit, so a read-only kopicode role can still edit.
  - Claude Code: Standard is `--allowedTools` patterns plus deny patterns for the
    never-allowed prefixes. Auto is `--allowedTools Bash` plus those denies and `curl` and
    `wget` (a deny pattern cannot say "piped into a shell", so downloads are refused
    outright). Ask first removes Bash. Read-only also denies `Edit`, `Write`, `MultiEdit`
    and `NotebookEdit`, which closes the V4-B gap for this backend.
  - Codex: Read-only and Ask first get `--sandbox read-only`; Standard and Auto get
    `workspace-write`. There is no per-command filter, so Auto adds nothing there and Ask
    first cannot edit either; the sandbox's own path and network limits are what hold the
    never-allowed list.

## Consequences

- *Amended by ADR-0028:* on kopicode, Ask first and Standard now ask (the text below is what
  was true when this was written). Claude Code and Codex still refuse.
- Ask first does not ask yet. Until V4-H a command that would need an answer is refused, and
  the dashboard must say so rather than imply a prompt.
- Auto on Claude Code is crude (no downloads at all) and its deny-beats-allow behaviour is not
  yet verified live, like the Standard mapping before it.
- Read-only is enforced on Claude Code and Codex, and is a shell restriction only on kopicode
  (known-gaps.md).
- Approving commands in Auto is a decision, not containment (ADR-0002): an approved
  `make test` still runs repository code as this user.

## Amendment (2026-10-11, CUT-90): a chained line is decided command by command

A live Standard-mode Codex run asked nine times for one small task, because coding agents chain
(`pwd && rg ... && git status`, a command per line, `cmd || true`) and a chained line could not be matched
to the list, so it could only be allowed once. `ConsentPolicy` now reads `&&`, `||`, `;`, `|` and newline as
separators and allows the line when every command in it is on the list, after the whole line has passed the
never-allowed check and each command has passed it again. Quoted arguments are accepted (single quotes
are literal), as are `2>&1`, `2>/dev/null` and `>/dev/null`, `true`/`false`, and `command -v <name>`. A
redirect to a file, `$`, a backtick, parentheses, a glob, a lone `&`, stray spaces or an unclosed quote
still leave the line unmatched, so it waits for a person. Inside a chain a bare `git commit` entry opens
only `git commit [-a] -m '<message>'`. Result: the same task asked six times, and every remaining prompt
was a command not on the list (`git config`, `rm`, a heredoc `git apply`).
