#!/bin/sh
# Install cuttlefish-crew for the current user, no sudo:
#
#   curl -fsSL https://raw.githubusercontent.com/leejianrong/cuttlefish-crew/main/scripts/install.sh | sh
#
# What it does, in order (ADR-0045):
#   1. Checks git, curl, Node/npm, tmux and a coding-agent CLI, and says which are missing.
#   2. Installs uv (https://astral.sh/uv) into ~/.local/bin if it is not there. uv also
#      fetches Python 3.12+ itself, so you do not need a system Python.
#   3. Clones (or updates) the repository into ~/.local/share/cuttlefish/app.
#   4. `uv tool install`s the `cuttlefish` command into ~/.local/bin.
#   5. Builds the dashboard into the checkout (needs Node 20.19+/22.12+ and npm; skipped with a warning if absent).
#   6. Runs `cuttlefish doctor`.
# It never uses sudo, never edits your shell profile, and never installs Node, tmux or an agent:
# those are your system's or the agent vendor's to install, so it names them and how.
# Running it again updates in place and changes nothing else.
#
# Options:   --check      only look at the prerequisites, change nothing
#            --uninstall  remove what this script installed (not uv, not your projects' data)
# Variables: CUTTLEFISH_INSTALL_DIR (checkout, default ~/.local/share/cuttlefish/app)
#            CUTTLEFISH_BIN_DIR     (default ~/.local/bin)
#            CUTTLEFISH_REPO_URL, CUTTLEFISH_REF (default: the GitHub repo, main)
#
# Uninstall: curl -fsSL <same url> | sh -s -- --uninstall
#   or by hand: uv tool uninstall cuttlefish && rm -rf ~/.local/share/cuttlefish
# Your projects' own .cuttlefish/ directories and ~/.cuttlefish (secrets, devices, logs) are left alone.

set -eu

REPO_URL="${CUTTLEFISH_REPO_URL:-https://github.com/leejianrong/cuttlefish-crew.git}"
REF="${CUTTLEFISH_REF:-main}"
BIN_DIR="${CUTTLEFISH_BIN_DIR:-$HOME/.local/bin}"
APP_DIR="${CUTTLEFISH_INSTALL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/cuttlefish/app}"

mode=install
case "${1:-}" in
	--check) mode=check ;;
	--uninstall) mode=uninstall ;;
	"") ;;
	*)
		echo "usage: install.sh [--check | --uninstall]" >&2
		exit 2
		;;
esac

say() { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() {
	printf 'error: %s\n' "$*" >&2
	exit 1
}
have() { command -v "$1" >/dev/null 2>&1; }

# The uv we install lands in BIN_DIR, so look there too.
PATH_ORIG="$PATH"
PATH="$BIN_DIR:$PATH"
export PATH

if [ "$mode" = uninstall ]; then
	if have uv; then uv tool uninstall cuttlefish >/dev/null 2>&1 || true; fi
	rm -rf "$APP_DIR"
	say "Removed the cuttlefish command and $APP_DIR. uv and ~/.cuttlefish are untouched."
	exit 0
fi

# --- prerequisites -------------------------------------------------------------------------
# `missing` collects hard requirements, `advice` the soft ones; both are printed together.
missing=""
advice=""

have git || missing="$missing
  - git: install it with your package manager (apt install git, brew install git)"
have curl || have wget || missing="$missing
  - curl: install it with your package manager"
if ! have uv; then
	say "uv: not found, will be installed to $BIN_DIR"
fi
# The dashboard builds with Vite 8, which needs Node 20.19+ or 22.12+ (or anything newer).
node_ok() {
	v="$(node --version 2>/dev/null | sed 's/^v//')"
	major="${v%%.*}"
	rest="${v#*.}"
	minor="${rest%%.*}"
	case "$major$minor" in '' | *[!0-9]*) return 1 ;; esac
	[ "$major" -gt 22 ] && return 0
	[ "$major" -eq 22 ] && [ "$minor" -ge 12 ] && return 0
	[ "$major" -eq 20 ] && [ "$minor" -ge 19 ] && return 0
	return 1
}
if have node && have npm; then
	node_ok || advice="$advice
  - Node.js 20.19+ or 22.12+ for the dashboard (found $(node --version)): https://nodejs.org or nvm"
else
	advice="$advice
  - Node.js 20.19+ or 22.12+ with npm, to build the dashboard: https://nodejs.org or nvm
    (the cuttlefish command works without them; the dashboard does not)"
fi
have tmux || advice="$advice
  - tmux, for the Agent session (an agent that outlives the browser): apt install tmux, brew install tmux"
if ! have kopicode && ! have claude && ! have codex; then
	advice="$advice
  - a coding-agent CLI on PATH: kopicode (curl -fsSL https://raw.githubusercontent.com/leejianrong/kopicode/main/scripts/install.sh | sh),
    claude (Claude Code) or codex"
fi

if [ -n "$missing" ]; then
	printf 'Cannot install, missing:%s\n' "$missing" >&2
	[ -z "$advice" ] || printf 'Also missing (not blocking):%s\n' "$advice" >&2
	exit 1
fi
if [ "$mode" = check ]; then
	say "Required prerequisites are present (git, curl or wget)."
	[ -z "$advice" ] || printf 'Not found (install later if you want that feature):%s\n' "$advice"
	exit 0
fi

# --- uv ------------------------------------------------------------------------------------
if ! have uv; then
	say "Installing uv with its own installer (https://astral.sh/uv/install.sh)..."
	if have curl; then
		curl -fsSL https://astral.sh/uv/install.sh | UV_INSTALL_DIR="$BIN_DIR" UV_NO_MODIFY_PATH=1 sh
	else
		wget -qO- https://astral.sh/uv/install.sh | UV_INSTALL_DIR="$BIN_DIR" UV_NO_MODIFY_PATH=1 sh
	fi
	have uv || die "uv did not install. See https://docs.astral.sh/uv/getting-started/installation/"
fi

# --- source --------------------------------------------------------------------------------
if [ -d "$APP_DIR/.git" ]; then
	say "Updating $APP_DIR to $REF..."
	git -C "$APP_DIR" fetch --quiet origin "$REF" || die "git fetch failed in $APP_DIR (offline?)"
	git -C "$APP_DIR" checkout --quiet --force FETCH_HEAD
else
	[ ! -e "$APP_DIR" ] || [ -z "$(ls -A "$APP_DIR" 2>/dev/null)" ] ||
		die "$APP_DIR exists and is not a cuttlefish checkout; move it or set CUTTLEFISH_INSTALL_DIR"
	say "Cloning $REPO_URL into $APP_DIR..."
	mkdir -p "$APP_DIR"
	git clone --quiet --depth 1 --branch "$REF" "$REPO_URL" "$APP_DIR" ||
		die "git clone failed (offline, or no such ref '$REF')"
fi

# --- command -------------------------------------------------------------------------------
say "Installing the cuttlefish command into $BIN_DIR (Python 3.12+, fetched by uv if needed)..."
UV_TOOL_BIN_DIR="$BIN_DIR" uv tool install --python ">=3.12" --reinstall "$APP_DIR" ||
	die "uv tool install failed; the output above says why"

# --- dashboard -----------------------------------------------------------------------------
dashboard=skipped
if have node && have npm && node_ok; then
	say "Building the dashboard (npm ci, npm run build; a minute or two)..."
	if (cd "$APP_DIR/frontend" && npm ci --no-audit --no-fund && npm run build); then
		dashboard=built
	else
		warn "the dashboard build failed; the output above says why. Re-run this script after fixing it."
		dashboard=failed
	fi
fi

# --- check ---------------------------------------------------------------------------------
say ""
say "Running: cuttlefish doctor"
"$BIN_DIR/cuttlefish" doctor || warn "cuttlefish doctor reported problems (see above)"

say ""
say "Installed: $BIN_DIR/cuttlefish (dashboard: $dashboard)"
case ":$PATH_ORIG:" in
	*":$BIN_DIR:"*) ;;
	*) say "Add $BIN_DIR to your PATH:  export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac
[ -z "$advice" ] || printf 'Still missing:%s\n' "$advice"
say "Next: cuttlefish serve"
