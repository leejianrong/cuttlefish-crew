#!/usr/bin/env bash
# The one-command way to see the dashboard: make demo (or ./scripts/demo.sh).
# Builds the dashboard once (if missing or stale), then runs `cuttlefish serve`
# alone -- ADR-0012 (KAN-1707): the daemon serves that build itself, same
# origin as the JSON API, so this is one process now, not two babysat ones.
# Ctrl-C stops it. `cuttlefish serve` itself already auto-picks a free port if
# its default is taken (find_free_port, cuttlefish.fleet.server).
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
frontend_dir="$repo_root/frontend"
dist_dir="$frontend_dir/dist"

if [ ! -d "$frontend_dir/node_modules" ]; then
	echo "Installing dashboard dependencies (frontend/node_modules missing)…"
	( cd "$frontend_dir" && npm install )
fi

# A plain mtime check, not a content hash -- cheap and correct enough for a
# local dev script (ADR-0012): rebuild if there's no build yet, or any
# frontend source file is newer than the build's own index.html.
needs_build=0
if [ ! -f "$dist_dir/index.html" ]; then
	needs_build=1
elif [ -n "$(find "$frontend_dir/src" -newer "$dist_dir/index.html" -print -quit 2>/dev/null)" ]; then
	needs_build=1
fi

if [ "$needs_build" -eq 1 ]; then
	echo "Building the dashboard (frontend/dist missing or stale)…"
	( cd "$frontend_dir" && npm run build )
fi

echo
echo "Starting cuttlefish serve -- open the URL it prints below, paste the"
echo "token into the connect screen, and you're in."
echo

cd "$repo_root"
exec uv run cuttlefish serve
