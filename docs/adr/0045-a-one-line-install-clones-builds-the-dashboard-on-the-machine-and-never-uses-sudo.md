# ADR-0045: A one-line install clones, builds the dashboard on the machine, and never uses sudo

- Status: Accepted (script written and tested; not yet hosted or announced)
- Date: 2026-10-31
- Deciders: Jian (card CUT-224), development agent

## Context

The landing page told people to `git clone` and `uv sync`. herdr, Paseo and Happy offer a one-line install or a
download (`docs/research/fleet-manager-landscape.md`, "setup friction"). Prerequisites: Python 3.12+, uv, Node/npm
for the dashboard, tmux for the Agent session, one coding-agent CLI.

## Decision

`scripts/install.sh` (POSIX sh, shellcheck-clean), run as `curl -fsSL <raw url> | sh`.

1. **Built on the machine, not prebuilt.** It clones the repository to `~/.local/share/cuttlefish/app` and runs
   `npm ci && npm run build` there. A prebuilt `frontend/dist` would need a release pipeline and a place to host
   it, and the script is not allowed to widen the deploy path (AGENTS.md). Cost: Node 20.19+ or 22.12+ (Vite 8)
   and a minute or two. Without Node the command still installs and the script says the dashboard is skipped.
2. **`uv tool install` for the command**, into `~/.local/bin`. No sudo, no profile edits; if `~/.local/bin` is not
   on `PATH` it prints the line to add. uv is installed with its own installer if missing, and uv fetches Python
   3.12+ itself, so a system Python is not a prerequisite.
3. **`cuttlefish serve` finds the dashboard without a checkout.** `_resolve_dashboard_dir` tries `./frontend/dist`,
   then `$XDG_DATA_HOME/cuttlefish/app/frontend/dist`.
4. **It installs only what it can without root.** git and curl are required and block the install with a plain
   message. Node, tmux and an agent CLI are named with how to get them and do not block; `cuttlefish doctor` is
   run at the end. It never installs an agent, or Node, or tmux.
5. **Idempotent.** A second run fetches the ref, rebuilds and reinstalls; a checkout it did not make (a non-empty
   directory without `.git`) is refused, not overwritten. Local edits in the install checkout are discarded
   (`checkout --force`): it is not a place to work.
6. **Uninstall:** `sh install.sh --uninstall`, or `uv tool uninstall cuttlefish && rm -rf ~/.local/share/cuttlefish`.
   `~/.cuttlefish` (secrets, devices, logs) and projects' own `.cuttlefish/` are never touched.
7. `--check` looks at prerequisites and changes nothing; `tests/unit/test_install_script.py` runs it under a
   controlled `PATH`.

## Consequences

- `main` is what installs. `CUTTLEFISH_REF` pins a tag or branch.
- Piping to sh is a trust decision; the script is short and commented at the top for that reason. Hosting it on a
  friendlier URL, and releases with a prebuilt dashboard, are later and separate.
- Not covered: Windows (use WSL), and macOS was not run (the script avoids GNU-only flags, but only Linux was tested).
